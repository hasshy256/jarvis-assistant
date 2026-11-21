"""音声認識。Arc GPU で動く OpenVINO 版 Whisper（medium）を使い、使えなければ CPU の faster-whisper（small）に戻る。

比べた結果（1 文あたり / 一致率）: CPU small 2.4 秒 95.9%、GPU large-v3 1.3 秒 96.7%、GPU medium 0.7 秒 97.4%。
"""
import os
import re
import threading

import numpy as np

from config import DATA_DIR, STT_ENGINE, STT_OPENVINO_MODEL, WHISPER_MODEL, WHISPER_VOCAB

WAKE_PREFIX = re.compile(r"^\s*(ねえ|ねぇ|ヘイ|おい)?[、,\s]*(ジャービス|ジャーヴィス|jarvis)[、。,.!！?？\s]*", re.I)
# 無音や雑音のときに Whisper がでっち上げやすい定型文
HALLUCINATIONS = ("ご視聴ありがとうございました", "チャンネル登録", "おやすみなさい", "字幕")

# 言葉のヒント。文として渡すほうが効く。無音のときにこれをそのまま出力することがあるので、後で弾く
PROMPT = "ジャービスへの依頼。" + "、".join(WHISPER_VOCAB) + "。"
# 「よろしいですか」と聞いたあと。短い返事は聞き間違えやすい（「はい」→「バイッ」など）ので、返事が来ると伝えておく
CONFIRM_PROMPT = "確認への返事。はい。いいえ。お願いします。やめて。大丈夫です。"


class STT:
    def __init__(self):
        self.engine = None
        self.lock = threading.Lock()  # 議事録の文字起こし（裏）と会話が同時に使わないように
        if STT_ENGINE == "openvino":
            try:
                self._init_openvino()
                self.engine = "openvino"
            except Exception as e:
                print(f"GPU の音声認識を使えないので CPU で動かします: {e}", flush=True)
        if self.engine is None:
            from faster_whisper import WhisperModel
            self.model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
            self.engine = "faster-whisper"

    def _init_openvino(self):
        import openvino as ov
        import openvino_genai
        from huggingface_hub import snapshot_download

        core = ov.Core()
        # 独立した GPU（Arc）を優先し、なければ内蔵 GPU
        gpus = [d for d in core.available_devices if d.startswith("GPU")]
        device = next((d for d in gpus if "dGPU" in core.get_property(d, "FULL_DEVICE_NAME")), gpus[0] if gpus else None)
        if device is None:
            raise RuntimeError("GPU が見つかりません")
        path = os.path.join(DATA_DIR, "models", STT_OPENVINO_MODEL.split("/")[-1])
        if not os.path.exists(os.path.join(path, "openvino_encoder_model.xml")):
            snapshot_download(STT_OPENVINO_MODEL, local_dir=path)
        self.pipe = openvino_genai.WhisperPipeline(path, device)
        self.device = core.get_property(device, "FULL_DEVICE_NAME")
        self._ov_generate(np.zeros(16000, np.float32), PROMPT)  # 1 回目は GPU の準備で遅いので、起動時に済ませる

    def _ov_generate(self, audio, prompt):
        cfg = self.pipe.get_generation_config()
        cfg.language, cfg.task = "<|ja|>", "transcribe"
        cfg.initial_prompt = prompt
        return str(self.pipe.generate(audio.tolist(), cfg)).strip()

    def transcribe(self, pcm16, confirm=False):
        """confirm=True は確認への返事を待っているとき（「はい／いいえ」を聞き取りやすくする）。"""
        audio = pcm16.astype(np.float32) / 32768
        prompt = CONFIRM_PROMPT if confirm else PROMPT
        with self.lock:
            if self.engine == "openvino":
                raw = self._ov_generate(audio, prompt)
            else:
                segments, _ = self.model.transcribe(audio, language="ja", vad_filter=True, beam_size=5,
                                                    initial_prompt=prompt, condition_on_previous_text=False)
                raw = "".join(s.text for s in segments)
        text = WAKE_PREFIX.sub("", raw).strip()
        if len(text) >= 8 and text.strip("。、 ") in prompt:  # ヒントをそのまま出力しただけ（無音など）
            return ""
        if any(h in text for h in HALLUCINATIONS) and len(text) < 20:
            return ""
        return text
