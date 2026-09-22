// Kestrel site. Small, deliberate interactions only.

document.addEventListener('DOMContentLoaded', () => {
  buildChatCycle();
  buildDocsRail();
});

// ── The chat mockup ──────────────────────────────────────────────────
//
// Cycles through short conversations, one per feature, rendered with the
// same structure and the same values as app/chat_widget.py. Each scene
// plays its steps in order, holds, then clears for the next one.
//
// Step kinds map one to one onto things the real chat puts in the feed:
//   chip  an attachment chip in the composer, before the message is sent
//   user  a user message (sending clears the chips, as it does in the app)
//   think the collapsed extended-thinking header
//   ai    an assistant reply, no bubble
//   card  the autonomous-mode confirmation card
//   file  the card shown when a file has been written
//   pill  a centred system line

function buildChatCycle() {
  const feed = document.querySelector('[data-chat-feed]');
  const chips = document.querySelector('[data-chat-chips]');
  const title = document.querySelector('[data-chat-title]');
  const scroll = document.querySelector('[data-chat-scroll]');
  if (!feed || !chips || !title || !scroll) return;

  const I = {
    copy: 'M256 64C220.7 64 192 92.7 192 128L192 384C192 419.3 220.7 448 256 448L448 448C483.3 448 512 419.3 512 384L512 128C512 92.7 483.3 64 448 64L256 64zM128 192C92.7 192 64 220.7 64 256L64 512C64 547.3 92.7 576 128 576L320 576C355.3 576 384 547.3 384 512L384 480L320 480L320 512L128 512L128 256L160 256L160 192L128 192z',
    speaker: 'M352 128C352 117.6 346 108.2 336.6 103.8C327.2 99.4 316.1 100.9 308.2 107.6L185.8 212L104 212C81.9 212 64 229.9 64 252L64 388C64 410.1 81.9 428 104 428L185.8 428L308.2 532.4C316.1 539.1 327.2 540.6 336.6 536.2C346 531.8 352 522.4 352 512L352 128zM445 200.9C432.6 209.5 429.5 226.5 438.1 238.9C454.4 262.4 464 291 464 322C464 353 454.4 381.6 438.1 405.1C429.5 417.5 432.6 434.5 445 443.1C457.4 451.7 474.4 448.6 483 436.2C505.9 403.1 519.4 363.1 519.4 322C519.4 280.9 505.9 240.9 483 207.8C474.4 195.4 457.4 192.3 445 200.9z',
    refresh: 'M112 208L112 128C112 110.3 126.3 96 144 96C161.7 96 176 110.3 176 128L176 169.6C219.1 123.3 279.9 96 345.6 96C469.8 96 570.6 195.6 572 319.5C573.4 445.4 470.2 548 344.3 548C281.5 548 224.6 522.6 183.4 481.5C170.9 469 170.9 448.7 183.4 436.2C195.9 423.7 216.2 423.7 228.7 436.2C258.4 465.9 299.3 484 344.3 484C438.6 484 514.6 405.9 508.1 310.3C502.4 226.6 429.6 160 345.6 160C295 160 248.6 184.4 219.6 224L272 224C289.7 224 304 238.3 304 256C304 273.7 289.7 288 272 288L144 288C126.3 288 112 273.7 112 256L112 208z',
    pen: 'M416.9 85.2L372 130.1L509.9 268L554.8 223.1C568.4 209.6 576 191.2 576 172C576 152.8 568.4 134.4 554.8 120.9L519.1 85.2C505.6 71.6 487.2 64 468 64C448.8 64 430.4 71.6 416.9 85.2zM338.1 164L122.9 379.1C112.2 389.8 104.4 403.2 100.3 417.8L64.9 545.6C62.6 553.9 64.9 562.9 71.1 569C77.3 575.1 86.2 577.5 94.5 575.2L222.3 539.7C236.9 535.6 250.2 527.9 261 517.1L476 301.9L338.1 164z',
    file: 'M192 64C156.7 64 128 92.7 128 128L128 512C128 547.3 156.7 576 192 576L448 576C483.3 576 512 547.3 512 512L512 240L352 240C334.3 240 320 225.7 320 208L320 64L192 64zM384 74.5L384 192L501.5 192L384 74.5zM208 320L432 320C440.8 320 448 327.2 448 336C448 344.8 440.8 352 432 352L208 352C199.2 352 192 344.8 192 336C192 327.2 199.2 320 208 320zM208 416L432 416C440.8 416 448 423.2 448 432C448 440.8 440.8 448 432 448L208 448C199.2 448 192 440.8 192 432C192 423.2 199.2 416 208 416z',
    brain: 'M232 96C198.9 96 172 122.9 172 156C172 158.5 172.2 161 172.5 163.4C145.1 172.6 125.3 198.5 125.3 229C125.3 241 128.4 252.3 133.8 262.1C111.8 273.3 96.7 296.2 96.7 322.7C96.7 349.5 112.2 372.7 134.7 383.8C132.5 390.6 131.3 397.8 131.3 405.3C131.3 440.7 158.4 469.8 193 472.9C199.7 505.2 228.3 529.3 262.7 529.3C295.8 529.3 324 506.4 331.7 475.6L332 156C332 122.9 305.1 96 272 96L232 96zM408 96C374.9 96 348 122.9 348 156L348.3 475.6C356 506.4 384.2 529.3 417.3 529.3C451.7 529.3 480.3 505.2 487 472.9C521.6 469.8 548.7 440.7 548.7 405.3C548.7 397.8 547.5 390.6 545.3 383.8C567.8 372.7 583.3 349.5 583.3 322.7C583.3 296.2 568.2 273.3 546.2 262.1C551.6 252.3 554.7 241 554.7 229C554.7 198.5 534.9 172.6 507.5 163.4C507.8 161 508 158.5 508 156C508 122.9 481.1 96 448 96L408 96z',
    bolt: 'M434.8 54.1C446.7 62.7 451.1 78.3 445.7 91.9L367.3 288L512 288C525.5 288 537.5 296.4 542.1 309.1C546.7 321.8 542.8 336 532.5 344.6L244.5 584.6C233.2 594 217.1 594.5 205.2 585.9C193.3 577.3 188.9 561.7 194.3 548.1L272.7 352L128 352C114.5 352 102.5 343.6 97.9 330.9C93.3 318.2 97.2 304 107.5 295.4L395.5 55.4C406.8 46 422.9 45.5 434.8 54.1z',
    globe: 'M320 64C178.6 64 64 178.6 64 320C64 461.4 178.6 576 320 576C461.4 576 576 461.4 576 320C576 178.6 461.4 64 320 64zM320 128C336.9 128 360.5 145.5 379.3 191.7C385 205.7 389.9 221.7 393.8 239.2L246.2 239.2C250.1 221.7 255 205.7 260.7 191.7C279.5 145.5 303.1 128 320 128zM402.1 303.2C403.3 314.1 403.9 325.6 403.9 337.4C403.9 353.6 402.7 369 400.7 383.4L239.3 383.4C237.3 369 236.1 353.6 236.1 337.4C236.1 325.6 236.7 314.1 237.9 303.2L402.1 303.2zM320 512C303.1 512 279.5 494.5 260.7 448.3C257.3 439.9 254.2 430.8 251.4 421.1L388.6 421.1C385.8 430.8 382.7 439.9 379.3 448.3C360.5 494.5 336.9 512 320 512zM466.5 383.4C468.1 368.6 469 353.2 469 337.4C469 325.6 468.5 314.2 467.6 303.2L509.8 303.2C513.1 313.9 514.9 325.2 514.9 337C514.9 353 512 368.7 506.6 383.4L466.5 383.4zM173.5 383.4L133.4 383.4C128 368.7 125.1 353 125.1 337C125.1 325.2 126.9 313.9 130.2 303.2L172.4 303.2C171.5 314.2 171 325.6 171 337.4C171 353.2 171.9 368.6 173.5 383.4zM459.6 239.2C450.3 195 434.1 157.4 412.9 130.6C453.2 148.1 486.6 179.1 507.2 218.1C511 225.4 514.3 232.2 517.1 239.2L459.6 239.2zM180.4 239.2L122.9 239.2C125.7 232.2 129 225.4 132.8 218.1C153.4 179.1 186.8 148.1 227.1 130.6C205.9 157.4 189.7 195 180.4 239.2zM227.1 509.4C186.8 491.9 153.4 460.9 132.8 421.9C132.5 421.6 132.4 421.4 132.2 421.1L180.9 421.1C190.2 463.4 206 498.8 227.1 509.4zM412.9 509.4C434 498.8 449.8 463.4 459.1 421.1L507.8 421.1C507.6 421.4 507.5 421.6 507.2 421.9C486.6 460.9 453.2 491.9 412.9 509.4z',
    plus: 'M352 128C352 110.3 337.7 96 320 96C302.3 96 288 110.3 288 128L288 288L128 288C110.3 288 96 302.3 96 320C96 337.7 110.3 352 128 352L288 352L288 512C288 529.7 302.3 544 320 544C337.7 544 352 529.7 352 512L352 352L512 352C529.7 352 544 337.7 544 320C544 302.3 529.7 288 512 288L352 288L352 128z',
    xmark: 'M183.1 137.4C170.6 124.9 150.3 124.9 137.8 137.4C125.3 149.9 125.3 170.2 137.8 182.7L275.2 320L137.9 457.4C125.4 469.9 125.4 490.2 137.9 502.7C150.4 515.2 170.7 515.2 183.2 502.7L320.5 365.3L457.9 502.6C470.4 515.1 490.7 515.1 503.2 502.6C515.7 490.1 515.7 469.8 503.2 457.3L365.8 320L503.1 182.6C515.6 170.1 515.6 149.8 503.1 137.3C490.6 124.8 470.3 124.8 457.8 137.3L320.5 274.7L183.1 137.4z',
    arrowUp: 'M342.6 73.4C330.1 60.9 309.8 60.9 297.3 73.4L137.3 233.4C124.8 245.9 124.8 266.2 137.3 278.7C149.8 291.2 170.1 291.2 182.6 278.7L288 173.3L288 544C288 561.7 302.3 576 320 576C337.7 576 352 561.7 352 544L352 173.3L457.4 278.7C469.9 291.2 490.2 291.2 502.7 278.7C515.2 266.2 515.2 245.9 502.7 233.4L342.7 73.4z',
    folder: 'M96 160C96 124.7 124.7 96 160 96L253.5 96C270.5 96 286.8 102.7 298.8 114.7L333.3 149.3C345.3 161.3 361.6 168 378.5 168L480 168C515.3 168 544 196.7 544 232L544 256L160 256C132.4 256 108.5 272.5 97.8 296.2L96 300.3L96 160zM123.8 305.9C129.3 293.9 141.3 286 154.5 286L556.3 286C580 286 596 310.3 586.7 332.1L508.7 515.1C500.6 534.1 481.9 546.4 461.2 546.4L83.7 546.4C60 546.4 44 522.1 53.3 500.3L123.8 305.9z',
  };

  const svg = (d, cls) =>
    `<svg viewBox="0 0 640 640" aria-hidden="true"${cls ? ` class="${cls}"` : ''}><path d="${d}"/></svg>`;

  const userActions =
    `<div class="kacts"><span class="kact">${svg(I.copy)}Copy</span>` +
    `<span class="kact">${svg(I.pen)}Edit</span></div>`;
  const aiActions =
    `<div class="kacts"><span class="kact">${svg(I.copy)}Copy</span>` +
    `<span class="kact">${svg(I.speaker)}Read aloud</span>` +
    `<span class="kact">${svg(I.refresh)}Regenerate</span></div>`;

  // Each scene shows one thing the app does.
  const scenes = [
    {
      title: 'Q3 report',
      steps: [
        { kind: 'chip', icon: I.file, text: 'q3-report.pdf' },
        { kind: 'user', text: 'Summarise this and pull the figures into a spreadsheet' },
        { kind: 'ai', text: 'Read 14 pages. Revenue is up <b>8%</b> on Q2, driven by renewals rather than new business. Support costs are flat.' },
        { kind: 'file', name: 'q3-figures.xlsx', detail: 'Created in Documents\\Kestrel Files' },
      ],
    },
    {
      title: 'Supplier contracts',
      steps: [
        { kind: 'chip', icon: I.file, text: 'contract-a.docx' },
        { kind: 'chip', icon: I.file, text: 'contract-b.docx' },
        { kind: 'user', text: 'Which of these two is worse for us, and where exactly?' },
        { kind: 'think', text: 'Thought for 9s' },
        { kind: 'ai', text: 'B. The liability cap in clause 11 is one month of fees against A\'s twelve, and clause 14 lets them change the rate on 30 days\' notice.' },
      ],
    },
    {
      title: 'Morning',
      steps: [
        { kind: 'user', text: 'Put on my morning playlist' },
        { kind: 'pill', icon: I.bolt, text: 'Ran macro: morning_playlist' },
        { kind: 'ai', text: 'Playing. That one came from the macro, so it did not need a screenshot.' },
      ],
    },
    {
      title: 'Dentist',
      steps: [
        { kind: 'user', text: 'Book me the first free slot on the dentist\'s site next week' },
        { kind: 'ai', text: 'That needs several steps in the browser. I can take it from here if you want.' },
        { kind: 'card', goal: 'Book the first free appointment next week on the dentist\'s booking site.' },
      ],
    },
    {
      title: 'Research',
      steps: [
        { kind: 'user', text: 'What changed in the EU AI Act this month?' },
        { kind: 'pill', icon: I.globe, text: 'Read 6 pages across 2 sites' },
        { kind: 'ai', text: 'Two things. The GPAI code of practice moved to its final text, and the transparency obligations in Article 50 now have published guidance.' },
      ],
    },
  ];

  const render = {
    chip(step) {
      const el = document.createElement('span');
      el.className = 'kchip kin';
      el.innerHTML = `${svg(step.icon)}<span>${step.text}</span><span class="x">${svg(I.xmark)}</span>`;
      chips.appendChild(el);
      return null;
    },
    user(step) {
      chips.innerHTML = '';
      return row('sent', `<div class="kbubble">${step.text}</div>${userActions}`);
    },
    think(step) {
      return row('recv', `<span class="kthink">${svg(I.brain)}▸&nbsp; ${step.text}</span>`);
    },
    ai(step) {
      return row('recv', `<div class="kai"><div>${step.text}</div></div>${aiActions}`);
    },
    pill(step) {
      return row('mid', `<span class="kpill">${svg(step.icon)}${step.text}</span>`);
    },
    file(step) {
      return row('recv',
        `<div class="kfile">${svg(I.file, 'ico')}` +
        `<div class="meta"><div class="name">${step.name}</div><div class="detail">${step.detail}</div></div>` +
        `<span class="kbtn-accept">${svg(I.folder, 'ico')} Show in folder</span></div>`);
    },
    card(step) {
      return row('recv',
        '<div class="kcard">' +
        '<div class="kcard-title">Switch to autonomous mode?</div>' +
        `<div class="kcard-goal">${step.goal}</div>` +
        '<div class="kcard-hint">Say « accept » or « refuse », or click a button.</div>' +
        '<div class="kcard-btns"><span class="kbtn-accept">Accept</span>' +
        '<span class="kbtn-cancel">Cancel</span></div></div>');
    },
  };

  function row(side, inner) {
    const el = document.createElement('div');
    el.className = `krow ${side} kin`;
    el.innerHTML = inner;
    feed.appendChild(el);
    return el;
  }

  // How long to leave a step on screen before the next one arrives.
  // A user message reads fast; a reply the viewer has to actually read
  // gets longer, and the last step of a scene holds longest.
  const dwell = { chip: 500, user: 900, think: 800, pill: 900, ai: 2600, file: 2200, card: 2600 };

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function paint(scene) {
    title.textContent = scene.title;
    chips.innerHTML = '';
    feed.innerHTML = '';
    scene.steps.forEach((s) => render[s.kind](s));
  }

  if (reduceMotion) {
    paint(scenes[0]);
    return;
  }

  let sceneIndex = 0;
  let stepIndex = 0;

  function begin() {
    const scene = scenes[sceneIndex];
    title.textContent = scene.title;
    chips.innerHTML = '';
    feed.innerHTML = '';
    stepIndex = 0;
    next();
  }

  function next() {
    const scene = scenes[sceneIndex];
    const step = scene.steps[stepIndex];
    render[step.kind](step);
    scroll.scrollTop = scroll.scrollHeight;
    stepIndex += 1;

    if (stepIndex < scene.steps.length) {
      setTimeout(next, dwell[step.kind] || 1200);
      return;
    }
    // End of a scene: hold on the finished exchange, fade, move on.
    setTimeout(() => {
      scroll.classList.add('fading');
      setTimeout(() => {
        sceneIndex = (sceneIndex + 1) % scenes.length;
        begin();
        scroll.classList.remove('fading');
      }, 320);
    }, (dwell[step.kind] || 1200) + 1400);
  }

  begin();
}

// ── Docs page: highlight the active section link on scroll ───────────

function buildDocsRail() {
  const navLinks = document.querySelectorAll('.docs-nav a[href^="#"]');
  if (!navLinks.length) return;

  const sections = Array.from(navLinks)
    .map((a) => document.querySelector(a.getAttribute('href')))
    .filter(Boolean);
  if (!sections.length) return;

  let ticking = false;
  const setActive = () => {
    let current = sections[0];
    for (const s of sections) {
      if (s.getBoundingClientRect().top - 96 <= 0) current = s;
    }
    navLinks.forEach((a) => {
      a.classList.toggle('active', a.getAttribute('href') === '#' + current.id);
    });
    ticking = false;
  };
  // rAF-throttled: getBoundingClientRect() on every section shouldn't
  // run more than once per frame.
  window.addEventListener('scroll', () => {
    if (!ticking) {
      window.requestAnimationFrame(setActive);
      ticking = true;
    }
  }, { passive: true });
  setActive();
}
