/* Общие UX-компоненты: навигация по замечаниям, раскрытие секций по якорю,
   полноэкранный предпросмотр, помощник установки значения select. */
(function () {
  'use strict';

  // --- «?»-подсказки: нативный Popover API с запасным вариантом ---------
  var nativePopover = 'showPopover' in HTMLElement.prototype;
  if (!nativePopover) document.documentElement.classList.add('no-popover');

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.help-btn');
    if (!btn) return;
    if (nativePopover) return;            // нативный popover откроется сам
    e.preventDefault();
    var pop = document.getElementById(btn.getAttribute('popovertarget'));
    if (!pop) return;
    var wasOpen = pop.classList.contains('is-open');
    document.querySelectorAll('.help-pop.is-open').forEach(function (p) {
      p.classList.remove('is-open');
    });
    if (!wasOpen) pop.classList.add('is-open');
    btn.setAttribute('aria-expanded', String(!wasOpen));
  });

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || nativePopover) return;
    document.querySelectorAll('.help-pop.is-open').forEach(function (p) {
      p.classList.remove('is-open');
    });
  });

  document.addEventListener('click', function (e) {
    if (nativePopover) return;
    if (e.target.closest('.help-btn') || e.target.closest('.help-pop')) return;
    document.querySelectorAll('.help-pop.is-open').forEach(function (p) {
      p.classList.remove('is-open');
    });
  });

  // --- Поиск по длинным справочникам (Tom Select, ленивая инициализация) --
  function initTomSelect(sel) {
    if (!sel || !window.TomSelect || sel.tomselect) return null;
    if (sel.options.length <= 8) return null;   // маленькие списки не трогаем
    var previous = window.stroySuppressDirty;
    window.stroySuppressDirty = true;
    try {
      var ts = new TomSelect(sel, {
        allowEmptyOption: true,
        maxOptions: 300,
        lockOptgroupOrder: true,
        create: false,
        placeholder: 'Начните вводить…',
      });
      sel.tomselect = ts;
      return ts;
    } catch (e) {
      return null;
    } finally {
      window.stroySuppressDirty = previous;
    }
  }
  window.stroyInitSelect = initTomSelect;

  document.addEventListener('focusin', function (e) {
    var sel = e.target.closest && e.target.closest('select[data-autocomplete]');
    if (sel) initTomSelect(sel);
  });
  // Первый клик заменяем на поиск сразу, не давая браузеру открыть нативный список.
  document.addEventListener('pointerdown', function (e) {
    if (!window.TomSelect) return;   // библиотека не загрузилась — оставляем нативный select
    var sel = e.target.closest && e.target.closest('select[data-autocomplete]');
    if (!sel || sel.tomselect || sel.options.length <= 8) return;
    e.preventDefault();
    var ts = initTomSelect(sel);
    if (ts) ts.focus();
  }, true);

  // --- Установка значения select с учётом autocomplete-обёртки -----------
  window.stroySetSelect = function (sel, value) {
    if (!sel) return;
    value = value == null ? '' : String(value);
    if (sel.tomselect) {
      sel.tomselect.setValue(value, true);
    } else {
      sel.value = value;
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    }
  };

  // --- Открыть целевую секцию при переходе по якорю ----------------------
  function openHashTarget() {
    var hash = window.location.hash;
    if (!hash || hash.length < 2) return;
    var target = document.querySelector(hash);
    if (target && target.tagName === 'DETAILS') target.open = true;
  }
  window.addEventListener('hashchange', openHashTarget);
  openHashTarget();

  // --- Клик по замечанию ведёт к секции, строке и полю -------------------
  document.addEventListener('click', function (e) {
    var link = e.target.closest('[data-warning-section]');
    if (!link) return;
    var section = document.getElementById(link.getAttribute('data-warning-section'));
    if (!section) return;
    e.preventDefault();

    if (section.tagName === 'DETAILS') section.open = true;
    section.scrollIntoView({ behavior: 'smooth', block: 'start' });

    var pk = link.getAttribute('data-warning-pk');
    var field = link.getAttribute('data-warning-field');
    var target = null;

    if (pk) {
      var row = section.querySelector('tr[data-pk="' + pk + '"]');
      if (row) {
        row.classList.add('row-warning');
        if (field) target = row.querySelector('[name$="-' + field + '"]');
      }
    }
    if (!target && field) {
      target = section.querySelector('[name="' + field + '"]')
        || section.querySelector('[name$="-' + field + '"]');
    }
    if (!target) {
      target = section.querySelector(
        'select, input:not([type=hidden]):not([type=checkbox]), textarea'
      );
    }
    if (target) {
      target.focus({ preventScroll: true });
      target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  });

  // Подсветить строки, на которые уже есть замечания.
  document.querySelectorAll('[data-warning-pk]').forEach(function (link) {
    var section = document.getElementById(link.getAttribute('data-warning-section'));
    var pk = link.getAttribute('data-warning-pk');
    if (!section || !pk) return;
    var row = section.querySelector('tr[data-pk="' + pk + '"]');
    if (row) row.classList.add('row-warning');
  });

  // --- Полноэкранный предпросмотр на узких экранах -----------------------
  var preview = document.getElementById('act-preview');
  var pvToggle = document.getElementById('pv-toggle');
  var pvClose = document.getElementById('pv-close');
  if (preview) {
    function syncLock() {
      document.body.classList.toggle('pv-locked', preview.classList.contains('pv-open'));
    }
    if (pvToggle) pvToggle.addEventListener('click', function () { setTimeout(syncLock, 0); });
    if (pvClose) pvClose.addEventListener('click', function () { preview.classList.remove('pv-open'); syncLock(); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && preview.classList.contains('pv-open')) {
        preview.classList.remove('pv-open');
        syncLock();
      }
    });
  }

  // --- Модальное окно «+ создать» ---------------------------------------
  var refModal = document.getElementById('ref-modal');
  var refFrame = document.getElementById('ref-modal-frame');
  var refLoading = document.getElementById('ref-modal-loading');
  var lastPopupTrigger = null;

  function closeRefModal() {
    if (refModal && refModal.open) refModal.close();
  }

  if (refModal && refFrame && typeof refModal.showModal === 'function') {
    document.addEventListener('click', function (e) {
      var link = e.target.closest('a[data-popup]');
      if (!link) return;
      e.preventDefault();
      lastPopupTrigger = link;
      if (refLoading) refLoading.hidden = false;
      refModal.showModal();
      refFrame.src = link.href;
    });

    refFrame.addEventListener('load', function () {
      if (refLoading && refFrame.src && refFrame.src !== 'about:blank') {
        refLoading.hidden = true;
      }
    });

    document.addEventListener('click', function (e) {
      if (e.target.closest('[data-modal-close]')) closeRefModal();
    });
    refModal.addEventListener('click', function (e) {
      if (e.target === refModal) closeRefModal();   // клик по фону
    });
    refModal.addEventListener('close', function () {
      refFrame.src = 'about:blank';
      if (lastPopupTrigger) lastPopupTrigger.focus();
      lastPopupTrigger = null;
    });

    window.addEventListener('message', function (e) {
      if (e.origin !== window.location.origin) return;
      var data = e.data || {};
      if (data.type === 'stroy-ref-saved' || data.type === 'stroy-ref-close') {
        closeRefModal();
      }
    });
  }

  // --- Новая запись из «+ создать» сразу появляется в списках ------------
  function targetSelectFor(trigger, model) {
    if (!trigger) return null;
    var field = trigger.closest('.field');
    if (field) {
      var inField = field.querySelector('select[data-model="' + model + '"]');
      if (inField) return inField;
    }
    var th = trigger.closest('th');
    if (th) {
      var index = Array.prototype.indexOf.call(th.parentElement.children, th);
      var table = th.closest('table');
      var row = table && table.querySelector('tbody tr');
      var cell = row && row.cells[index];
      if (cell) return cell.querySelector('select[data-model="' + model + '"]');
    }
    return null;
  }

  function addSavedOption(saved) {
    if (!saved || !saved.model || !saved.pk) return;
    var selects = document.querySelectorAll('select[data-model="' + saved.model + '"]');
    var target = targetSelectFor(lastPopupTrigger, saved.model);

    selects.forEach(function (sel) {
      var exists = Array.prototype.some.call(sel.options, function (o) {
        return String(o.value) === String(saved.pk);
      });
      if (!exists) {
        var opt = document.createElement('option');
        opt.value = saved.pk;
        opt.textContent = saved.label || saved.pk;
        sel.appendChild(opt);
      }
      if (sel.tomselect && typeof sel.tomselect.sync === 'function') {
        try { sel.tomselect.sync(); } catch (e) {}
      }
    });

    if (target) {
      window.stroySetSelect(target, saved.pk);
      if (window.stroyMarkDirty) window.stroyMarkDirty();
    } else if (selects.length === 1) {
      window.stroySetSelect(selects[0], saved.pk);
      if (window.stroyMarkDirty) window.stroyMarkDirty();
    }
  }

  if ('BroadcastChannel' in window) {
    new BroadcastChannel('stroy-ref').addEventListener('message', function (e) {
      addSavedOption(e.data);
    });
  }

  // --- Подтверждение действия с замечаниями ------------------------------
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-confirm]');
    if (!btn) return;
    if (!window.confirm(btn.getAttribute('data-confirm'))) e.preventDefault();
  });

  // --- Индикатор несохранённых изменений ---------------------------------
  var dirtyIndicator = document.getElementById('dirty-indicator');
  var dirtyForm = document.querySelector('form[data-dirty-guard]');
  if (dirtyIndicator && dirtyForm) {
    dirtyForm.addEventListener('input', function () {
      if (window.stroySuppressDirty) return;
      dirtyIndicator.textContent = 'Есть несохранённые изменения';
      dirtyIndicator.classList.add('is-dirty');
    });
    dirtyForm.addEventListener('submit', function () {
      dirtyIndicator.textContent = 'Сохранение…';
      dirtyIndicator.classList.remove('is-dirty');
    });
  }
})();
