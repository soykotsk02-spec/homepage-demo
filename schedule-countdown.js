"use strict";

// Display the existing daily 09:00 Asia/Shanghai plan. This clock does not
// start jobs or claim that the computer has run or delivered an email.
(() => {
  const cards = [...document.querySelectorAll("[data-agent-countdown]")];
  if (!cards.length) return;
  const day = 24 * 60 * 60 * 1000;
  const beijingOffset = 8 * 60 * 60 * 1000;
  const startHour = 9 * 60 * 60 * 1000;
  const dateLabel = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", month: "long", day: "numeric", weekday: "short",
  });
  const pad = number => String(number).padStart(2, "0");
  let timer = null;

  function update() {
    const now = Date.now();
    const beijingMidnight = Math.floor((now + beijingOffset) / day) * day - beijingOffset;
    let next = beijingMidnight + startHour;
    if (next <= now) next += day;
    const remaining = Math.ceil((next - now) / 1000);
    const hours = Math.floor(remaining / 3600);
    const minutes = Math.floor((remaining % 3600) / 60);
    const seconds = remaining % 60;
    for (const card of cards) {
      const value = card.querySelector("[data-countdown-value]");
      const planned = card.querySelector("[data-countdown-next]");
      if (value) {
        value.textContent = `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
        value.setAttribute("aria-label", `距下次计划启动还有 ${hours} 小时 ${minutes} 分 ${seconds} 秒`);
      }
      if (planned) planned.textContent = `下次计划：${dateLabel.format(next)} 09:00 · 北京时间`;
    }
  }

  function stop() {
    if (timer !== null) window.clearInterval(timer);
    timer = null;
  }
  function resume() {
    stop();
    update();
    if (!document.hidden) timer = window.setInterval(update, 1000);
  }
  document.addEventListener("visibilitychange", resume);
  window.addEventListener("pagehide", stop);
  window.addEventListener("pageshow", resume);
  resume();
})();
