import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../schedule-countdown.js', import.meta.url), 'utf8');

function mount(time, count = 2) {
  let now = Date.parse(time);
  let nextId = 0;
  const intervals = new Map();
  const listeners = {};
  const cards = Array.from({ length: count }, () => {
    const value = { textContent: '', setAttribute(name, text) { this[name] = text; } };
    const planned = { textContent: '' };
    return { value, planned, querySelector: key => key === '[data-countdown-value]' ? value : planned };
  });
  const document = { hidden: false, querySelectorAll: () => cards,
    addEventListener(name, callback) { listeners[name] = callback; } };
  const window = { setInterval(callback) { intervals.set(++nextId, callback); return nextId; },
    clearInterval(id) { intervals.delete(id); },
    addEventListener(name, callback) { listeners[name] = callback; } };
  class Clock extends Date { static now() { return now; } }
  vm.runInNewContext(source, { document, window, Date: Clock, Intl });
  return { cards, intervals, document, listeners,
    time(value) { now = Date.parse(value); },
    tick() { for (const callback of intervals.values()) callback(); } };
}

test('targets 09:00 Beijing across the UTC date boundary', () => {
  const ui = mount('2026-09-26T23:30:00+08:00');
  assert.equal(ui.cards[0].value.textContent, '09:30:00');
  assert.match(ui.cards[0].planned.textContent, /9月27日.*09:00.*北京时间/);
  assert.equal(ui.cards[1].value.textContent, ui.cards[0].value.textContent);
});

test('at 09:00 advances to the following day without claiming delivery', () => {
  const ui = mount('2026-09-30T08:59:59+08:00');
  assert.equal(ui.cards[0].value.textContent, '00:00:01');
  ui.time('2026-09-30T09:00:00+08:00');
  ui.tick();
  assert.equal(ui.cards[0].value.textContent, '24:00:00');
  assert.match(ui.cards[0].planned.textContent, /下次计划：10月1日/);
});

test('tab resume recomputes elapsed days and does not duplicate timers', () => {
  const ui = mount('2026-09-27T08:00:00+08:00');
  ui.document.hidden = true;
  ui.listeners.visibilitychange();
  assert.equal(ui.intervals.size, 0);
  ui.time('2026-10-02T08:50:00+08:00');
  ui.document.hidden = false;
  ui.listeners.visibilitychange();
  ui.listeners.pageshow();
  assert.equal(ui.intervals.size, 1);
  assert.equal(ui.cards[0].value.textContent, '00:10:00');
  assert.match(ui.cards[0].value['aria-label'], /0 小时 10 分 0 秒/);
  ui.listeners.pagehide();
  assert.equal(ui.intervals.size, 0);
});

test('pages without a schedule do not start a timer', () => {
  assert.equal(mount('2026-09-27T08:00:00+08:00', 0).intervals.size, 0);
});
