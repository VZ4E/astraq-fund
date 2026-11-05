'use strict';
const $ = id => document.getElementById(id);
let election, registry = [], pending;
const text = (tag, value) => { const el = document.createElement(tag); el.textContent = value; return el; };
async function request(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Service unavailable.');
  return data;
}
function renderRegistry() {
  const query = $('registry-search').value.toLocaleLowerCase();
  const matching = registry.filter(v => v.display_name.toLocaleLowerCase().includes(query));
  $('voters').replaceChildren(...matching.map(v => text('li', v.display_name)));
  $('registry-status').textContent = `${election?.demo ? 'DEMO DATA • ' : ''}${registry.length} accepted voter${registry.length === 1 ? '' : 's'}${query ? ` • ${matching.length} matching` : ''}`;
}
async function loadRegistry() {
  try { const data = await request('/api/registry'); registry = data.voters; renderRegistry(); }
  catch { $('registry-status').textContent = 'Registry unavailable. No voter count can be verified.'; }
}
async function start() {
  try {
    election = await request('/api/status');
    $('mode').textContent = election.demo ? 'DEMO ONLY — Sample nominees and a separate test database. Nothing here counts toward the awards. Use code DEMO-2026-PREVIEW-CODE to test one submission.' : election.open ? 'Voting is open for approved members. Your access code accepts one final ballot.' : 'Voting is closed. The approved roster, nominees, voting window, and independent review are still required.';
    $('mode').className = election.demo ? 'demo-banner' : 'status';
    $('ballot-form').hidden = !election.open;
    if (election.demo) $('access-code').value = 'DEMO-2026-PREVIEW-CODE';
    for (const category of election.categories) {
      const field = document.createElement('fieldset');
      field.append(text('legend', category.name));
      for (const nominee of [...category.nominees, {id:'abstain', name:'Abstain from this category'}]) {
        const label = document.createElement('label'); label.className = 'option';
        const input = document.createElement('input'); input.type = 'radio'; input.name = category.id; input.value = nominee.id; input.required = true;
        label.append(input, text('span', nominee.name)); field.append(label);
      }
      $('ballot-categories').append(field);
    }
  } catch { $('mode').textContent = 'Voting service is not connected. No ballots can be submitted.'; }
  await loadRegistry();
}
$('registry-search').addEventListener('input', renderRegistry);
$('ballot-form').addEventListener('submit', event => {
  event.preventDefault();
  const data = new FormData(event.target);
  pending = {token:data.get('token').trim(), choices:{}, consent:$('consent').checked};
  const lines = election.categories.map(category => {
    const id = data.get(category.id); pending.choices[category.id] = id;
    const nominee = category.nominees.find(n => n.id === id);
    return text('p', `${category.name}: ${nominee ? nominee.name : 'Abstain'}`);
  });
  $('review-choices').replaceChildren(...lines); $('review').hidden = false; $('ballot-form').hidden = true;
  $('ballot-message').textContent = ''; $('back').focus();
});
$('back').addEventListener('click', () => { $('review').hidden = true; $('ballot-form').hidden = false; pending = null; });
$('submit-vote').addEventListener('click', async () => {
  $('submit-vote').disabled = true; $('back').disabled = true;
  try {
    const result = await request('/api/ballots', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pending)});
    $('ballot-message').textContent = `${result.demo ? 'Demo ballot' : 'Ballot'} accepted. Save your receipt: ${result.receipt}. Your choices are not published.`;
    $('review').hidden = true; $('access-code').value = ''; pending = null;
    await loadRegistry();
  } catch (error) { $('ballot-message').textContent = error.message; }
  finally { $('submit-vote').disabled = false; $('back').disabled = false; }
});
start();
