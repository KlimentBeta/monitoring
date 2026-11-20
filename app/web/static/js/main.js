// Общие вспомогательные функции веб-интерфейса WebsiteMonitor.
(function () {
  "use strict";

  // Перевод Unix-миллисекунд в локальное время "дд.мм.гггг чч:мм:сс".
  window.wmFormatTime = function (iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "—";
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return p(d.getDate()) + "." + p(d.getMonth() + 1) + "." + d.getFullYear() +
           " " + p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  };

  window.wmFormatMs = function (v) {
    if (v === null || v === undefined) return "—";
    return v >= 1000 ? (v / 1000).toFixed(2) + " с" : Number(v).toFixed(1) + " мс";
  };

  // Автозакрытие flash-сообщений
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".flash").forEach(function (el) {
      setTimeout(function () {
        el.style.transition = "opacity .4s";
        el.style.opacity = "0";
        setTimeout(function () { el.remove(); }, 450);
      }, 6000);
    });
  });
})();