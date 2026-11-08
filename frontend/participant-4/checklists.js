import { api, escape as e, date, error, toast, busy } from '../shared/api.js';

const types = { binary: 'Да / Нет', numeric: 'Числовая шкала', flag: 'Флаг нарушения' };

export async function checklistsPage(root) {
  const lists = await api('/checklists');
  root.innerHTML = `<div class="page-heading"><div><div class="eyebrow">ПРАВИЛА ОЦЕНКИ</div><h1>Чек-листы</h1><p>Критерии меняются здесь. Каждое сохранение создаёт новую версию.</p></div><a class="button primary" href="#checklist">＋ Создать чек-лист</a></div>
    <div class="checklist-grid">${lists.map(c => `<article class="panel checklist-card"><div class="card-top"><span class="document-icon">☷</span><span class="pill">Версия ${c.version}</span></div><h2>${e(c.name)}</h2><p>${e(c.description)}</p><div class="checklist-numbers"><span><strong>${c.criteria.length}</strong> критериев</span><span><strong>${c.criteria.reduce((sum, item) => sum + item.weight, 0)}</strong> сумма весов</span></div><div class="card-bottom"><small>${date(c.created_at)}</small><a class="button small" href="#checklist?id=${e(c.id)}">Открыть →</a></div></article>`).join('')}</div>
    <div class="notice wide-notice">Оценки сохраняют свою версию чек-листа. Изменения применяются к новым оценкам и не переписывают прошлые результаты.</div>`;
}

const field = (name, label, value, attrs = '') => `<label>${label}<input name="${name}" value="${e(value)}" ${attrs}></label>`;
function scaleRow(step = {}) {
  return `<div class="scale-row">${field('scale_value', 'Ступень', step.value ?? '', 'type="number" min="0" step="any" required')}${field('scale_description', 'Описание ступени', step.description ?? '', 'required maxlength="2000"')}<button type="button" class="icon-button remove-step" aria-label="Удалить ступень">×</button></div>`;
}
function criterionCard(c, number) {
  return `<article class="panel criterion-editor" data-id="${e(c.id)}"><div class="criterion-editor-heading"><span class="criterion-number">${String(number).padStart(2, '0')}</span><h3>Критерий</h3><button type="button" class="text-button danger remove-criterion">Удалить</button></div><div class="panel-body">
    ${field('name', 'Название критерия', c.name, 'required maxlength="200"')}
    <label>Правило оценки<textarea name="description" required maxlength="5000" rows="3">${e(c.description)}</textarea></label>
    <div class="form-grid">${field('section', 'Секция (необязательно)', c.section, 'maxlength="200"')}${field('weight', 'Вес', c.weight, `type="number" step="any" min="0" max="1000" required ${c.answer_type === 'flag' ? 'readonly' : ''}`)}<label>Тип ответа<select name="answer_type">${Object.entries(types).map(([value, label]) => `<option value="${value}" ${c.answer_type === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label></div>
    <div class="na-settings"><label class="checkbox-label"><input type="checkbox" name="allow_na" ${c.allow_na ? 'checked' : ''} ${c.answer_type === 'flag' ? 'disabled' : ''}>Разрешить N/A</label><label class="na-condition" ${c.allow_na ? '' : 'hidden'}>Когда критерий неприменим<textarea name="na_condition" rows="2" maxlength="2000" ${c.allow_na ? 'required' : ''}>${e(c.na_condition)}</textarea></label></div>
    <div class="scale-settings" ${c.answer_type === 'numeric' ? '' : 'hidden'}><h4>Шкала оценки</h4><p class="muted">Укажите допустимые ступени и их смысл. Шкала начинается с 0; верхняя ступень даёт полный вес.</p><div class="scale-rows">${c.scale.map(scaleRow).join('')}</div><button type="button" class="button small add-step">＋ Добавить ступень</button></div>
    </div></article>`;
}

export async function checklistPage(root, params) {
  const id = params.get('id');
  const checklist = id ? await api(`/checklists/${encodeURIComponent(id)}`) : { name: '', description: '', criteria: [], version: null };
  root.innerHTML = `<a class="back-link" href="#checklists">← Все чек-листы</a><div class="page-heading"><div><div class="eyebrow">НАСТРОЙКА КРИТЕРИЕВ</div><h1>${id ? 'Редактор чек-листа' : 'Новый чек-лист'}</h1><p>${id ? `Версия ${checklist.version} · исходная версия останется в истории` : 'Задайте правила, по которым система будет оценивать диалоги.'}</p></div><span class="pill" id="weight-total"></span></div>
    <form id="checklist-form"><section class="panel panel-body checklist-info">${field('title', 'Название чек-листа', checklist.name, 'required maxlength="200"')}<label>Описание<textarea name="summary" maxlength="5000" rows="3">${e(checklist.description)}</textarea></label></section>
      <div id="criteria-editors">${checklist.criteria.map((c, i) => criterionCard(c, i + 1)).join('')}</div>
      <button type="button" class="add-criterion" id="add-criterion">＋ Добавить критерий</button>
      <div id="save-error"></div><div class="save-bar"><span id="save-status">${id ? `Сохранённая версия ${checklist.version}` : 'Новый чек-лист ещё не сохранён'}</span><button type="submit" class="button primary">${id ? 'Сохранить новую версию' : 'Создать чек-лист'}</button></div>
    </form>`;
  const form = root.querySelector('#checklist-form'), list = root.querySelector('#criteria-editors');
  function updateWeight() {
    const sum = [...list.querySelectorAll('[name="weight"]')].reduce((result, input) => result + (Number(input.value) || 0), 0);
    root.querySelector('#weight-total').textContent = `Сумма весов: ${Math.round(sum * 100) / 100}`;
  }
  function bindCard(card) {
    card.querySelector('.remove-criterion').onclick = () => { card.remove(); updateWeight(); };
    const na = card.querySelector('[name="allow_na"]'), naRule = card.querySelector('[name="na_condition"]');
    const setNa = () => { card.querySelector('.na-condition').hidden = !na.checked; naRule.required = na.checked; if (!na.checked) naRule.value = ''; };
    na.onchange = setNa;
    const scale = card.querySelector('.scale-rows');
    const bindSteps = () => scale.querySelectorAll('.remove-step').forEach(button => { button.onclick = () => button.closest('.scale-row').remove(); });
    bindSteps();
    card.querySelector('.add-step').onclick = () => { scale.insertAdjacentHTML('beforeend', scaleRow()); bindSteps(); };
    card.querySelector('[name="answer_type"]').onchange = event => {
      const numeric = event.target.value === 'numeric', flag = event.target.value === 'flag';
      const weight = card.querySelector('[name="weight"]');
      weight.readOnly = flag; if (flag) weight.value = '0';
      na.disabled = flag; if (flag) na.checked = false; setNa();
      card.querySelector('.scale-settings').hidden = !numeric;
      if (numeric && !scale.children.length) { scale.innerHTML = scaleRow({ value: 0 }) + scaleRow(); bindSteps(); }
      scale.querySelectorAll('input').forEach(input => { input.disabled = !numeric; });
      updateWeight();
    };
  }
  list.querySelectorAll('.criterion-editor').forEach(bindCard);
  root.querySelector('#add-criterion').onclick = () => {
    list.insertAdjacentHTML('beforeend', criterionCard({ id: crypto.randomUUID(), name: '', description: '', section: '', weight: 0, answer_type: 'binary', allow_na: false, na_condition: '', scale: [] }, list.children.length + 1));
    bindCard(list.lastElementChild); list.lastElementChild.querySelector('[name="name"]').focus(); updateWeight();
  };
  form.addEventListener('input', () => { root.querySelector('#save-status').textContent = 'Есть несохранённые изменения'; updateWeight(); });
  form.onsubmit = async event => {
    event.preventDefault(); const button = form.querySelector('[type="submit"]'); const done = busy(button, 'Сохраняем…');
    const criteria = [...list.children].map(card => {
      const value = name => card.querySelector(`[name="${name}"]`).value;
      const allow = card.querySelector('[name="allow_na"]').checked;
      return { id: card.dataset.id, name: value('name'), description: value('description'), section: value('section'), weight: Number(value('weight')), answer_type: value('answer_type'), allow_na: allow, na_condition: allow ? value('na_condition') : '',
        scale: value('answer_type') === 'numeric' ? [...card.querySelectorAll('.scale-row')].map(row => ({ value: Number(row.querySelector('[name="scale_value"]').value), description: row.querySelector('[name="scale_description"]').value })) : [] };
    });
    try {
      const body = { name: form.elements.title.value, description: form.elements.summary.value, criteria };
      if (id) body.base_version = checklist.version;
      const saved = await api(id ? `/checklists/${id}` : '/checklists', { method: id ? 'PUT' : 'POST', body });
      toast(`Чек-лист сохранён · версия ${saved.version}`);
      if (id) await checklistPage(root, params); else location.hash = `#checklist?id=${saved.id}`;
    } catch (exception) { error(root.querySelector('#save-error'), exception.message); } finally { done(); }
  };
  updateWeight();
}
