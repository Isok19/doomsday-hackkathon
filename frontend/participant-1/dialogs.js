import { api, query, escape as e, date, error, toast, busy, safeUrl, dialogLink } from '../shared/api.js';
import { renderEvaluation } from './results.js';

export async function dialogsPage(root) {
  const [datasets, capabilities] = await Promise.all([api('/datasets'), api('/capabilities')]);
  let group = datasets.find(d => d.dataset_group_id === sessionStorage.getItem('cqa-group'))?.dataset_group_id || datasets[0]?.dataset_group_id || '';
  root.innerHTML = `<div class="page-heading"><div><div class="eyebrow">АНАЛИЗ КАЧЕСТВА</div><h1>Диалоги</h1><p>От разговора к понятной, проверяемой оценке.</p></div><div><button class="button" id="export-button">Выгрузить результаты</button> <button class="button primary" id="import-button">＋ Загрузить диалоги</button></div></div>
    <div class="stats" id="stats"></div>
    <section class="panel"><div class="panel-heading"><div><h2>Все диалоги</h2><p>Выберите разговор, чтобы изучить транскрипт и результаты.</p></div><span class="pill">RU · KZ · EN</span></div>
      <form class="filters"><label class="search-label"><span class="sr-only">Поиск по ID</span><input name="q" placeholder="Поиск по ID диалога" type="search"></label>
      <label>Набор<select name="dataset_group_id">${datasets.map(d => `<option ${d.dataset_group_id === group ? 'selected' : ''} value="${e(d.dataset_group_id)}">${e(d.dataset_group_id)}</option>`).join('')}</select></label>
      <label>Язык<select name="language"><option value="">Все языки</option><option>RU</option><option>KZ</option><option>EN</option></select></label>
      <label>Канал<select name="channel"><option value="">Все каналы</option><option value="agent chat">Чат</option><option value="voice call">Звонок</option></select></label>
      <button class="button" type="submit">Найти</button></form>
      <div id="dialog-list" aria-live="polite"></div><div class="pagination"><span id="page-count"></span><div><button class="button small" id="previous">← Назад</button><button class="button small" id="next">Далее →</button></div></div>
    </section><div class="footnote">Оцениваются действия оператора. Сообщения бота и системные события сохраняются как контекст.</div>
    <dialog id="import-dialog"><div class="modal-heading"><h2>Загрузка диалогов</h2><button class="icon-button" data-close aria-label="Закрыть">×</button></div><p>Загрузите JSON в формате набора организаторов. Максимальный размер — 10 МБ.</p>
      <form id="upload-form"><label class="upload-zone">JSON с транскриптами<input name="file" type="file" accept=".json,application/json" required></label><div class="form-error"></div><button class="button primary">Загрузить в систему</button></form>
      <div class="divider"></div><h3>Получить через API организаторов</h3><form id="fetch-form"><label>group_id<input name="group_id" value="DEV" required></label><p class="muted">${capabilities.organizer_import_available ? 'Данные будут загружены сервером по настроенному адресу.' : 'Подключение API организаторов пока не настроено. Вы можете загрузить выданный JSON выше.'}</p><button class="button" ${capabilities.organizer_import_available ? '' : 'disabled'}>Получить набор</button><div class="form-error"></div></form>
    </dialog>
    <dialog id="export-dialog"><div class="modal-heading"><h2>Результаты для сдачи</h2><button class="icon-button" data-close aria-label="Закрыть">×</button></div>
      <p>Выгружаются последние результаты по исходному чек-листу v1 с учётом решений по апелляциям. Набор выбран в фильтре списка.</p>
      <form id="export-form"><label>Файл для сдачи<select name="split"><option value="dev">DEV — 65 диалогов</option><option value="prod">PROD — 10 контрольных чатов</option></select></label>
      <p class="muted">Файл results.json нужно сохранить в results/dev/ или results/prod/ и отправить в Git. Скачивание само по себе не выполняет push.</p>
      <div class="form-error"></div><button class="button primary">Скачать JSON</button></form>
    </dialog>`;
  let offset = 0, sequence = 0, total = 0;
  const form = root.querySelector('.filters');
  function stats() {
    const data = datasets.find(d => d.dataset_group_id === group);
    const cards = [['Диалогов в наборе', data?.total_records || 0, 'Все разговоры'], ['Чаты', data?.by_channel['agent chat'] || 0, 'Письменные обращения'], ['Звонки', data?.by_channel['voice call'] || 0, 'С транскриптами'], ['Сообщений', data?.total_messages || 0, 'Исходные реплики']];
    root.querySelector('#stats').innerHTML = cards.map(([title, number, subtitle]) => `<article class="stat"><span>${title}</span><strong>${number.toLocaleString('ru-RU')}</strong><small>${subtitle}</small></article>`).join('');
  }
  async function load() {
    const ticket = ++sequence;
    const filters = Object.fromEntries(new FormData(form)); group = filters.dataset_group_id || '';
    sessionStorage.setItem('cqa-group', group); stats();
    const box = root.querySelector('#dialog-list'); box.innerHTML = '<div class="empty compact">Загружаем диалоги…</div>';
    try {
      const page = await api(`/dialogs?${query({ ...filters, limit: 12, offset })}`);
      if (ticket !== sequence) return;
      total = page.total;
      box.innerHTML = page.items.length ? `<div class="table-scroll"><table><thead><tr><th>Диалог</th><th>Канал</th><th>Язык</th><th>Сообщения</th><th>Запись</th><th></th></tr></thead><tbody>${page.items.map(d => `<tr><td><a class="dialog-name" href="${dialogLink(d.dataset_group_id, d.conversation_id)}"><span class="conversation-icon">${d.channel === 'voice call' ? '◉' : '▤'}</span><span>${e(d.conversation_id)}<small>Набор ${e(d.dataset_group_id)}</small></span></a></td><td>${d.channel === 'voice call' ? 'Звонок' : 'Чат'}</td><td><span class="language">${e(d.language)}</span></td><td>${d.message_count}</td><td>${d.has_audio ? '<span class="green-text">Есть ссылка</span>' : '<span class="muted">—</span>'}</td><td><a class="row-link" href="${dialogLink(d.dataset_group_id, d.conversation_id)}" aria-label="Открыть ${e(d.conversation_id)}">↗</a></td></tr>`).join('')}</tbody></table></div>` : '<div class="empty"><span class="empty-symbol">▤</span><h3>Диалоги не найдены</h3><p>Измените фильтры или загрузите набор данных.</p></div>';
      root.querySelector('#page-count').textContent = total ? `${offset + 1}–${Math.min(offset + 12, total)} из ${total}` : '0 диалогов';
      root.querySelector('#previous').disabled = offset === 0;
      root.querySelector('#next').disabled = offset + 12 >= total;
    } catch (exception) { if (ticket === sequence) error(box, exception.message); }
  }
  form.addEventListener('submit', event => { event.preventDefault(); offset = 0; load(); });
  form.querySelectorAll('select').forEach(select => select.addEventListener('change', () => { offset = 0; load(); }));
  root.querySelector('#previous').onclick = () => { offset = Math.max(0, offset - 12); load(); };
  root.querySelector('#next').onclick = () => { offset += 12; load(); };
  const modal = root.querySelector('#import-dialog');
  root.querySelector('#import-button').onclick = () => modal.showModal();
  modal.querySelector('[data-close]').onclick = () => modal.close();
  modal.querySelector('#upload-form').onsubmit = async event => {
    event.preventDefault(); const current = event.currentTarget; const file = current.elements.file.files[0];
    if (!file) return;
    const done = busy(current.querySelector('button'), 'Загрузка…');
    try {
      if (file.size > 10 * 1024 * 1024) throw new Error('Файл превышает 10 МБ');
      const result = await api('/datasets/import', { method: 'POST', body: await file.text() });
      sessionStorage.setItem('cqa-group', result.dataset_group_id); modal.close(); toast(`Набор ${result.dataset_group_id}: ${result.total_records} диалогов`); await dialogsPage(root);
    } catch (exception) { error(current.querySelector('.form-error'), exception.message); } finally { done(); }
  };
  modal.querySelector('#fetch-form').onsubmit = async event => {
    event.preventDefault(); const current = event.currentTarget; const done = busy(current.querySelector('button'), 'Получаем данные…');
    try {
      const result = await api(`/datasets/fetch?${query({ group_id: current.elements.group_id.value })}`, { method: 'POST' });
      sessionStorage.setItem('cqa-group', result.dataset_group_id); modal.close(); toast('Набор загружен'); await dialogsPage(root);
    } catch (exception) { error(current.querySelector('.form-error'), exception.message); } finally { done(); }
  };
  const exportModal = root.querySelector('#export-dialog');
  root.querySelector('#export-button').onclick = () => exportModal.showModal();
  exportModal.querySelector('[data-close]').onclick = () => exportModal.close();
  exportModal.querySelector('form').onsubmit = async event => {
    event.preventDefault(); const current = event.currentTarget;
    const done = busy(current.querySelector('button'), 'Проверяем…');
    try {
      const data = await api(`/submissions?${query({dataset_group_id: group, split: current.elements.split.value})}`);
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2) + '\n'], {type: 'application/json;charset=utf-8'}));
      const link = document.createElement('a'); link.href = url; link.download = 'results.json'; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      const failures = data.results.filter(row => row.status === 'error').length;
      toast(`Файл подготовлен: ${data.results.length} диалогов, ошибок: ${failures}. Проверьте файл перед сдачей.`);
      exportModal.close();
    } catch (exception) { error(current.querySelector('.form-error'), exception.message); } finally { done(); }
  };
  await load();
}

export function transcript(dialog) {
  const roles = { client: 'Клиент', operator: 'Оператор', bot: 'Бот', system: 'Система' };
  return `<div class="transcript">${dialog.transcript.map(message => `<article id="message-${e(message.message_id)}" tabindex="-1" class="message ${message.speaker}"><header><strong>${roles[message.speaker]}</strong><time>${e(message.timestamp)}</time><span class="message-position">#${message.position}</span></header><div>${e(message.text)}</div></article>`).join('')}</div>`;
}

export async function dialogPage(root, params) {
  const group = params.get('group'), id = params.get('id');
  const [dialog, checklists, evaluations, capabilities] = await Promise.all([
    api(`/dialogs/${encodeURIComponent(id)}?${query({ dataset_group_id: group })}`), api('/checklists'),
    api(`/evaluations?${query({ dataset_group_id: group, conversation_id: id })}`), api('/capabilities'),
  ]);
  const audio = safeUrl(dialog.audio_url);
  const audioSource = `/api/dialogs/${encodeURIComponent(id)}/audio?${query({ dataset_group_id: group })}`;
  const selectedEvaluation = evaluations.find(item => item.id === params.get('evaluation'))?.id || evaluations[0]?.id;
  root.innerHTML = `<a href="#dialogs" class="back-link">← Все диалоги</a><div class="page-heading"><div><div class="eyebrow">КАРТОЧКА ДИАЛОГА</div><h1>${e(id)}</h1><p>${dialog.channel === 'voice call' ? 'Звонок' : 'Чат'} <span class="middle-dot">·</span> ${e(dialog.language)} <span class="middle-dot">·</span> ${dialog.message_count} сообщений <span class="middle-dot">·</span> ${e(group)}</p></div><span class="pill">${evaluations.length ? 'Есть результаты' : 'Без оценки'}</span></div>
    <div class="detail-grid"><section class="panel transcript-panel"><div class="panel-heading"><h2>Транскрипт</h2><span class="muted">Исходные сообщения</span></div>${audio ? `<div class="audio-panel"><audio controls preload="none" src="${e(audioSource)}"></audio><a href="${e(audio)}" target="_blank" rel="noopener">Открыть запись ↗</a><div class="audio-error muted" hidden>Запись не удалось воспроизвести здесь. Откройте её по ссылке или <button type="button" class="button small" id="retry-audio">Повторить</button>.</div></div>` : ''}${transcript(dialog)}</section>
    <section class="evaluation-panel"><div class="panel"><div class="panel-heading"><div><h2>Оценка качества</h2><p>По выбранной версии чек-листа</p></div></div><div class="panel-body"><form id="run-form"><label>Чек-лист<select name="checklist" required>${checklists.map(c => `<option value="${e(c.id)}">${e(c.name)} · v${c.version}</option>`).join('')}</select></label><button class="button primary full" ${capabilities.evaluation_available ? '' : 'disabled'}>${capabilities.evaluation_available ? 'Оценить диалог' : 'Автооценка пока недоступна'}</button></form>${capabilities.evaluation_available ? `<p class="notice">Модель: ${e(capabilities.evaluation_model || 'подключённый оценщик')}. Оценка ИИ требует проверки человеком.</p>` : '<p class="notice">Для запуска ИИ нужен ключ API и включение оценщика на сервере. Результаты не создаются автоматически.</p>'}<div id="run-error"></div></div></div>
      ${evaluations.length ? `<label class="history-select">Сохранённые оценки<select id="evaluation-select">${evaluations.map(v => `<option value="${e(v.id)}" ${v.id === selectedEvaluation ? 'selected' : ''}>${date(v.created_at)} · чек-лист v${v.checklist_version}</option>`).join('')}</select></label>` : ''}
      <div id="evaluation-content">${evaluations.length ? '' : '<div class="panel empty"><span class="empty-symbol">◎</span><h3>Оценок пока нет</h3><p>Здесь будут баллы, объяснения и ссылки на доказательства.</p></div>'}</div>
    </section></div>`;
  root.querySelector('audio')?.addEventListener('error', () => { root.querySelector('.audio-error').hidden = false; });
  root.querySelector('#retry-audio')?.addEventListener('click', () => {
    root.querySelector('.audio-error').hidden = true;
    root.querySelector('audio').load();
  });
  const output = root.querySelector('#evaluation-content');
  async function loadEvaluation(value) { try { await renderEvaluation(output, await api(`/evaluations/${value}`), dialog); } catch (exception) { error(output, exception.message); } }
  root.querySelector('#evaluation-select')?.addEventListener('change', event => loadEvaluation(event.target.value));
  if (selectedEvaluation) await loadEvaluation(selectedEvaluation);
  root.querySelector('#run-form').onsubmit = async event => {
    event.preventDefault(); const form = event.currentTarget; const done = busy(form.querySelector('button'), 'Оцениваем…');
    try {
      const checklist = checklists.find(c => c.id === form.elements.checklist.value);
      await api('/evaluations/run', { method: 'POST', body: { dataset_group_id: group, conversation_id: id, checklist_id: checklist.id, checklist_version: checklist.version } });
      toast('Оценка сохранена'); await dialogPage(root, params);
    } catch (exception) { error(root.querySelector('#run-error'), exception.message); } finally { done(); }
  };
}
