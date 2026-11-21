// 以前の版の「画面の保存」を片付けるだけのサービスワーカー。保存した画面を消して、自分を外す。
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.map((k) => caches.delete(k))))
    .then(() => self.registration.unregister()));
});
