document.addEventListener('DOMContentLoaded', () => {
  const sidebar = document.getElementById('sidebar');
  const backdrop = document.getElementById('sidebarBackdrop');
  const menuButton = document.getElementById('menuButton');
  const toggleMenu = (open) => {
    sidebar?.classList.toggle('open', open);
    backdrop?.classList.toggle('open', open);
    menuButton?.setAttribute('aria-expanded', String(open));
    menuButton?.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
  };
  menuButton?.addEventListener('click', () => toggleMenu(!sidebar.classList.contains('open')));
  backdrop?.addEventListener('click', () => toggleMenu(false));
  document.addEventListener('keydown', e => { if (e.key === 'Escape') toggleMenu(false); });

  document.querySelectorAll('.password-toggle').forEach(button => {
    button.addEventListener('click', () => {
      const input = button.parentElement.querySelector('input');
      const visible = input.type === 'text';
      input.type = visible ? 'password' : 'text';
      button.setAttribute('aria-label', visible ? 'Show password' : 'Hide password');
    });
  });
  document.getElementById('fillDemo')?.addEventListener('click', () => {
    document.getElementById('id_username').value = 'demo@brainmax.app';
    document.getElementById('id_password').value = 'BrainMax2026!';
    document.getElementById('id_password').focus();
  });

  const precision = document.getElementById('precisionRange');
  const speed = document.getElementById('speedRange');
  const updateScore = () => {
    if (!precision || !speed) return;
    const p = Number(precision.value) / 100;
    const v = Number(speed.value) / 100;
    const score = Math.round(100 * (0.7 * p + 0.3 * v));
    document.getElementById('precisionValue').textContent = `${precision.value} %`;
    document.getElementById('speedValue').textContent = `${speed.value} %`;
    document.getElementById('calculatedScore').textContent = score;
    document.getElementById('scoreInterpretation').textContent = score >= 80 ? 'Excellent — shoot for the next level.' : score >= 60 ? 'Solid — consistency makes the difference.' : 'In progress — prioritize accuracy.';
  };
  precision?.addEventListener('input', updateScore);
  speed?.addEventListener('input', updateScore);
  updateScore();

  document.querySelectorAll('[data-filter]').forEach(button => {
    button.addEventListener('click', () => {
      document.querySelectorAll('[data-filter]').forEach(b => b.classList.remove('active'));
      button.classList.add('active');
      const filter = button.dataset.filter;
      document.querySelectorAll('[data-category]').forEach(card => {
        card.hidden = filter !== 'all' && card.dataset.category !== filter;
      });
    });
  });

  document.querySelectorAll('[data-start-exercise]').forEach(button => {
    button.addEventListener('click', () => {
      const title = button.dataset.startExercise;
      const modalTitle = document.getElementById('exerciseModalTitle');
      const modalBody = document.getElementById('exerciseModalBody');
      if (modalTitle && modalBody) {
        modalTitle.textContent = title;
        modalBody.innerHTML = `<p>This interface prepares the <strong>${title}</strong> exercise. Interactive game logic will be connected in the next step.</p><div class="session-steps"><div>1. Read instructions</div><div>2. Respond at your pace</div><div>3. Review your score</div></div>`;
        bootstrap.Modal.getOrCreateInstance(document.getElementById('exerciseModal')).show();
      }
    });
  });

  const chatForm = document.getElementById('chatForm');
  chatForm?.addEventListener('submit', e => {
    e.preventDefault();
    const input = document.getElementById('chatInput');
    const text = input.value.trim();
    if (!text) return;
    const messages = document.getElementById('chatMessages');
    messages.insertAdjacentHTML('beforeend', `<div class="chat-message user"><div class="chat-avatar">You</div><div class="chat-bubble"><p></p></div></div>`);
    messages.lastElementChild.querySelector('p').textContent = text;
    input.value = '';
    const reply = document.createElement('div');
    reply.className = 'chat-message';
    reply.innerHTML = '<div class="chat-avatar">AI</div><div class="chat-bubble"><p>Your greatest asset is consistency. For this week, try two short memory sessions and one focus session. OpenAI integration will provide deeper insights in a future update.</p></div>';
    setTimeout(() => { messages.appendChild(reply); messages.scrollTop = messages.scrollHeight; }, 250);
    messages.scrollTop = messages.scrollHeight;
  });
});
