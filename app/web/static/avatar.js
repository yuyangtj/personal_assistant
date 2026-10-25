// The avatar: a small round mascot drawn in SVG, showing what the assistant is doing (idle,
// listening, thinking, speaking) and how its last reply felt. Loaded only when switched on
// (see setAvatar in events.js). The rest of the console talks to it through avatarEvent;
// nothing else touches the drawing.
(() => {
  const EMOTIONS = {
    // brows (lift left/right, tilt: inner end up > 0), smile (mouth curve), ears (angle),
    // tilt (head, degrees), hop (px up)
    Warm: { lift: [0, 0], tilt: 0, smile: 6, ears: 0, head: 0, hop: 0 },
    Curious: { lift: [-1, -6], tilt: 0, smile: 2, ears: 4, head: -6, hop: 0 },
    Excited: { lift: [-5, -5], tilt: 0, smile: 10, ears: 10, head: 0, hop: 4 },
    Concerned: { lift: [-2, -2], tilt: 14, smile: -3, ears: -14, head: 3, hop: 0 },
    Neutral: { lift: [0, 0], tilt: 0, smile: 1, ears: 0, head: 0, hop: 0 },
  };

  // Mouth shapes (visemes): width, how open, roundness, and what shows inside.
  const VISEMES = {
    rest: { w: 1.0, open: 0, round: 0 },
    A: { w: 1.1, open: 1, round: 0, tongue: true }, // a, i
    E: { w: 1.3, open: 0.5, round: 0, teeth: true }, // e, and light consonants
    O: { w: 0.78, open: 0.85, round: 1 }, // o
    U: { w: 0.56, open: 0.45, round: 1 }, // u, w, oo
    M: { w: 0.95, open: 0, round: 0, pressed: true }, // m, b, p
    F: { w: 1.0, open: 0.25, round: 0, teeth: true, lip: true }, // f, v
    L: { w: 1.0, open: 0.6, round: 0, tongue: true, teeth: true }, // l, t, d, n, th
    S: { w: 1.15, open: 0.2, round: 0, teeth: true }, // s, z, ch, sh, j
  };
  const PAIRS = { th: "L", oo: "U", ee: "E", ch: "S", sh: "S", ph: "F", ou: "O", ow: "O" };
  const LETTERS = {
    a: "A", i: "A", e: "E", o: "O", u: "U", w: "U", q: "U", y: "E",
    m: "M", b: "M", p: "M", f: "F", v: "F",
    l: "L", t: "L", d: "L", n: "L",
    s: "S", z: "S", c: "S", x: "S", j: "S",
    k: "E", g: "E", h: "E", r: "E",
    å: "O", ä: "E", ö: "O",
  };
  const VOWELS = new Set(["A", "E", "O", "U"]);

  // A word as mouth shapes, each with how long it lasts relative to the others.
  function visemesFor(word) {
    const text = String(word || "").toLowerCase().replace(/[^a-zåäö]/g, "");
    const shapes = [];
    for (let i = 0; i < text.length; i++) {
      const pair = PAIRS[text.slice(i, i + 2)];
      const shape = pair || LETTERS[text[i]];
      if (pair) i++;
      if (!shape) continue;
      if (shapes.length && shapes[shapes.length - 1].v === shape) {
        shapes[shapes.length - 1].weight += 0.4; // "ll", "ee": one longer shape
        continue;
      }
      shapes.push({ v: shape, weight: VOWELS.has(shape) ? 1.2 : 0.8 });
    }
    return shapes;
  }

  const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // A Live2D model (Ren) instead of the drawing, when the server has one: each mouth shape
  // as how open and what form (-1 round .. 1 wide), and each mood as one of its expressions.
  const LIVE2D_MOUTH = {
    rest: [0, 0], A: [1, 0.3], E: [0.5, 1], O: [0.8, -0.6], U: [0.4, -1],
    M: [0, 0], F: [0.2, 0.2], L: [0.6, 0.4], S: [0.2, 0.8],
  };
  const LIVE2D_EXPRESSION = {
    Warm: "exp_01", Curious: "exp_01", Excited: "exp_02", Concerned: "exp_05", Neutral: "exp_01",
  };

  // The model's parameters for a pose: a pure function, so the mapping can be checked
  // without a model. `mouth` is the eased { open, form }; `bob` the speaking nod (0..1).
  // "+Param" adds to the model's own motion, "Param" replaces it.
  function live2dParams(p, mouth = { open: 0, form: 0 }, bob = 0) {
    const params = {
      ParamMouthOpenY: mouth.open,
      ParamMouthForm: mouth.form,
      "+ParamAngleZ": -p.tilt,
      "+ParamAngleY": (p.lean ? 6 : 0) + (p.chinHand ? 4 : 0) + bob * 4,
    };
    if (p.dots) Object.assign(params, { ParamEyeBallX: 0.5, ParamEyeBallY: 0.6 });
    if (p.ring) Object.assign(params, { ParamEyeBallX: 0, ParamEyeBallY: 0 });
    return params;
  }
  // Where the mouth is heading for a pose: the shape while speaking, else the mood's smile.
  function live2dMouthTarget(p) {
    if (p.viseme !== "rest" && p.open > 0) {
      // How open comes from the pose (a still, half-open mouth with reduced motion).
      const [, form] = LIVE2D_MOUTH[p.viseme] || LIVE2D_MOUTH.A;
      return { open: p.open, form };
    }
    return { open: 0, form: Math.max(-1, Math.min(1, p.smile / 8)) };
  }

  // The shape for a moment, from what it's doing and feeling: a pure function, so a pose
  // can be checked without any timing. viseme: a mouth shape name, or 0..1 for how open.
  function pose(state, emotion, viseme = "rest", blink = 0) {
    const feel = EMOTIONS[emotion] || EMOTIONS.Warm;
    const shape = typeof viseme === "number"
      ? { ...VISEMES.A, open: Math.max(0, Math.min(1, viseme)) }
      : VISEMES[viseme] || VISEMES.rest;
    const speaking = state === "speaking";
    const listening = state === "listening";
    const thinking = state === "thinking";
    return {
      browY: feel.lift.map((lift) => 74 + lift - (listening ? 3 : 0)),
      browTilt: feel.tilt,
      eyeOpen: Math.max(0.08, (listening ? 1.12 : 1) * (1 - blink)),
      pupilDx: thinking ? 3 : 0,
      pupilDy: thinking ? -4 : 0,
      smile: speaking ? Math.min(feel.smile, 4) : feel.smile,
      open: speaking ? shape.open : 0,
      viseme: speaking ? (typeof viseme === "number" ? "A" : viseme) : "rest",
      ears: feel.ears + (listening ? 12 : 0),
      lean: listening ? 4 : 0,
      tilt: feel.head + (thinking ? -4 : 0),
      hop: feel.hop,
      chinHand: thinking,
      ring: listening,
      dots: thinking,
    };
  }

  // How a whole reply moves the mouth when the phone doesn't say which word it is on: its
  // words' shapes in order, with a short rest between words.
  function trackFor(text) {
    const track = [];
    for (const word of String(text || "").split(/\s+/)) {
      for (const step of visemesFor(word)) track.push(step);
      if (track.length) track.push({ v: "rest", weight: 0.5 });
    }
    return track;
  }

  const SVG = `<svg class="avatar-face" viewBox="0 0 200 200" role="img" aria-label="Assistant">
    <defs>
      <clipPath id="av-body-clip"><path d="${bodyPath()}"></path></clipPath>
      <clipPath id="av-mouth-clip"><path class="av-mouth-shape"></path></clipPath>
    </defs>
    <circle class="avatar-ring" cx="100" cy="108" r="88"></circle>
    <ellipse class="av-shadow" cx="100" cy="186" rx="50" ry="7"></ellipse>
    <g class="av-all">
      <g class="av-sway">
        <g class="av-ear" data-side="0"><ellipse cx="54" cy="36" rx="17" ry="20" transform="rotate(-22 54 36)"></ellipse>
          <ellipse class="av-ear-in" cx="54" cy="37" rx="8.5" ry="11.5" transform="rotate(-22 54 37)"></ellipse></g>
        <g class="av-ear" data-side="1"><ellipse cx="146" cy="36" rx="17" ry="20" transform="rotate(22 146 36)"></ellipse>
          <ellipse class="av-ear-in" cx="146" cy="37" rx="8.5" ry="11.5" transform="rotate(22 146 37)"></ellipse></g>
        <ellipse class="av-arm" data-side="0" cx="32" cy="140" rx="11" ry="16"></ellipse>
        <ellipse class="av-arm" data-side="1" cx="168" cy="140" rx="11" ry="16"></ellipse>
        <path class="av-body" d="${bodyPath()}"></path>
        <g clip-path="url(#av-body-clip)">
          <ellipse class="av-shade" cx="186" cy="124" rx="62" ry="104"></ellipse>
          <ellipse class="av-belly" cx="100" cy="172" rx="42" ry="28"></ellipse>
          <ellipse class="av-light" cx="66" cy="62" rx="24" ry="14" transform="rotate(-24 66 62)"></ellipse>
        </g>
        <g class="av-face">
          <ellipse class="av-cheek" cx="60" cy="120" rx="10" ry="6"></ellipse>
          <ellipse class="av-cheek" cx="140" cy="120" rx="10" ry="6"></ellipse>
          <g class="av-eye" data-side="0"><ellipse class="av-white" cx="76" cy="98" rx="14" ry="16"></ellipse>
            <g class="av-iris-group"><circle class="av-iris" cx="78" cy="100" r="9.5"></circle>
              <circle class="av-glint" cx="81.5" cy="95.5" r="3.4"></circle>
              <circle class="av-glint" cx="74.5" cy="104" r="1.6"></circle></g></g>
          <g class="av-eye" data-side="1"><ellipse class="av-white" cx="124" cy="98" rx="14" ry="16"></ellipse>
            <g class="av-iris-group"><circle class="av-iris" cx="122" cy="100" r="9.5"></circle>
              <circle class="av-glint" cx="125.5" cy="95.5" r="3.4"></circle>
              <circle class="av-glint" cx="118.5" cy="104" r="1.6"></circle></g></g>
          <path class="av-brow" data-side="0"></path>
          <path class="av-brow" data-side="1"></path>
          <ellipse class="av-hand" cx="121" cy="146" rx="11" ry="9"></ellipse>
          <g class="av-mouth">
            <path class="av-mouth-shape av-mouth-fill"></path>
            <g clip-path="url(#av-mouth-clip)">
              <rect class="av-teeth" x="80" y="116" width="40" height="7" rx="2"></rect>
              <ellipse class="av-tongue" cx="100" cy="146" rx="13" ry="9"></ellipse>
            </g>
            <path class="av-lip-line"></path>
          </g>
        </g>
      </g>
    </g>
    <g class="avatar-dots"><circle cx="164" cy="34" r="4"></circle><circle cx="176" cy="22" r="5">
      </circle><circle cx="190" cy="9" r="6"></circle></g></svg>`;

  // A soft, slightly squashed bean: wider at the bottom, gently domed on top.
  function bodyPath() {
    return "M100 26 C150 26 172 62 172 110 C172 158 142 182 100 182 "
      + "C58 182 28 158 28 110 C28 62 50 26 100 26 Z";
  }

  const face = {
    stage: null, mode: "svg", l2d: { open: 0, form: 0 }, expression: null,
    state: "idle", emotion: "Warm", blink: 0, lastReply: null,
    // The mouth as numbers that ease toward the current shape, so it never snaps.
    mouth: { w: 1, open: 0, round: 0 }, viseme: "rest",
    track: null, word: null, frame: null, blinkTimer: null, glance: 0, started: performance.now(),
  };

  const ease = (from, to, k) => from + (to - from) * k;

  function mouthPath(m, smile) {
    const cx = 100, cy = 128;
    const halfW = 15 * m.w;
    const curve = smile * 0.85; // corners up when smiling
    if (m.open < 0.04) {
      // Closed: a lip line that smiles (or frowns).
      return { shape: "", line: `M${cx - halfW} ${cy - curve * 0.4} Q${cx} ${cy + curve} ${cx + halfW} ${cy - curve * 0.4}` };
    }
    const height = 4 + m.open * 18;
    const top = cy - curve * 0.3 - height * 0.25 * (1 - m.round * 0.4);
    const bottom = cy + height * (0.85 + m.round * 0.1) + curve * 0.2;
    // Rounder shapes pull the corners in and the sides out.
    const side = halfW * (1 - m.round * 0.25);
    return {
      shape: `M${cx - side} ${cy - curve * 0.3} C${cx - side} ${top} ${cx + side} ${top} ${cx + side} ${cy - curve * 0.3} `
        + `C${cx + side * (1 + m.round * 0.15)} ${bottom} ${cx - side * (1 + m.round * 0.15)} ${bottom} ${cx - side} ${cy - curve * 0.3} Z`,
      line: "",
    };
  }

  function drawLive2d(p, now) {
    const still = reducedMotion();
    const target = live2dMouthTarget(p);
    const k = still ? 1 : 0.35;
    face.l2d = { open: ease(face.l2d.open, target.open, k), form: ease(face.l2d.form, target.form, k) };
    const t = (now - face.started) / 1000;
    const bob = !still && face.state === "speaking" ? Math.abs(Math.sin(t * 7)) : 0;
    window.live2dAvatar.setParams(live2dParams(p, face.l2d, bob));
    const expression = LIVE2D_EXPRESSION[face.emotion] || "exp_01";
    if (expression !== face.expression && window.live2dAvatar.ready()) {
      face.expression = expression;
      window.live2dAvatar.setExpression(expression);
    }
  }

  function draw(now = performance.now()) {
    if (!face.stage) return;
    const still = reducedMotion();
    const p = pose(face.state, face.emotion, face.viseme, face.blink);
    Object.assign(face.stage.dataset, {
      state: face.state, emotion: face.emotion, viseme: p.viseme, open: p.open.toFixed(2),
    });
    if (face.mode === "live2d") { drawLive2d(p, now); return; }
    const svg = face.stage.querySelector("svg");
    const t = (now - face.started) / 1000;
    // Idle life: breathing, a gentle sway; still with reduced motion.
    const breath = still ? 0 : Math.sin(t * 1.6) * 0.012;
    const sway = still ? 0 : Math.sin(t * 0.7) * 1.2;
    const bob = !still && face.state === "speaking" ? Math.abs(Math.sin(t * 7)) * 1.4 : 0;
    const hop = still ? 0 : p.hop * Math.max(0, Math.sin(t * 3));
    svg.dataset.state = face.state;
    svg.dataset.emotion = face.emotion;
    svg.dataset.viseme = p.viseme;
    svg.dataset.open = p.open.toFixed(2);
    const all = svg.querySelector(".av-all");
    all.setAttribute("transform",
      `translate(0 ${(-hop - bob - p.lean * 0.5).toFixed(2)}) rotate(${(p.tilt + sway).toFixed(2)} 100 182) `
      + `translate(100 182) scale(${(1 - breath).toFixed(4)} ${(1 + breath).toFixed(4)}) translate(-100 -182)`);
    svg.querySelectorAll(".av-ear").forEach((ear, side) => {
      const angle = (side ? 1 : -1) * (-p.ears + (still ? 0 : Math.sin(t * 2.3 + side) * 1.5));
      ear.setAttribute("transform", `rotate(${angle.toFixed(2)} ${side ? 134 : 66} 52)`);
    });
    // Thinking: one hand comes up to the chin (drawn in front), its arm tucked away.
    svg.querySelectorAll(".av-arm").forEach((arm, side) => {
      arm.style.display = p.chinHand && side === 1 ? "none" : "";
      arm.setAttribute("transform", `rotate(${(side ? -1 : 1) * (10 + sway)} ${side ? 166 : 34} 126)`);
    });
    svg.querySelector(".av-hand").style.display = p.chinHand ? "" : "none";
    // Eyes: blink by squashing, look by moving the iris.
    const glance = face.state === "idle" ? face.glance : 0;
    svg.querySelectorAll(".av-eye").forEach((eye) => {
      const cy = 98;
      eye.setAttribute("transform",
        `translate(0 ${cy}) scale(1 ${p.eyeOpen.toFixed(3)}) translate(0 ${-cy})`);
      eye.querySelector(".av-iris-group").setAttribute("transform",
        `translate(${(p.pupilDx + glance).toFixed(2)} ${p.pupilDy})`);
    });
    svg.querySelectorAll(".av-brow").forEach((brow, side) => {
      const [outer, inner] = side ? [140, 112] : [60, 88];
      const y = p.browY[side];
      const lift = p.browTilt / 3;
      brow.setAttribute("d", `M${outer} ${y + 2} Q${(outer + inner) / 2} ${y - 4} ${inner} ${y - lift}`);
    });
    // The mouth eases toward its shape.
    const target = VISEMES[p.viseme] || VISEMES.rest;
    const k = still ? 1 : 0.35;
    face.mouth = {
      w: ease(face.mouth.w, target.w, k),
      open: ease(face.mouth.open, p.open, k),
      round: ease(face.mouth.round, target.round, k),
    };
    if (p.viseme === "rest") face.mouth.open = p.open; // close cleanly when done
    const { shape, line } = mouthPath(face.mouth, p.smile);
    svg.querySelectorAll(".av-mouth-shape").forEach((path) => path.setAttribute("d", shape || "M0 0"));
    svg.querySelector(".av-lip-line").setAttribute("d", line || "M0 0");
    svg.querySelector(".av-teeth").style.display = target.teeth && shape ? "" : "none";
    svg.querySelector(".av-tongue").style.display = target.tongue && shape ? "" : "none";
    svg.querySelector(".avatar-ring").classList.toggle("on", p.ring);
    svg.querySelector(".avatar-dots").classList.toggle("on", p.dots);
  }

  // The current mouth shape from the word being spoken, or else the reply's whole track.
  function currentViseme(now) {
    const source = face.word || face.track;
    if (!source) return "rest";
    const elapsed = Math.max(0, now - source.started);
    let at = 0;
    for (const step of source.steps) {
      at += step.weight * source.unit;
      if (elapsed < at) return step.v;
    }
    if (source === face.word) { face.word = null; return face.track ? currentViseme(now) : "rest"; }
    face.track = null;
    if (face.state === "speaking") face.state = "idle";
    return "rest";
  }

  function loop(now) {
    if (!face.stage) return;
    if (face.state === "speaking") face.viseme = currentViseme(now);
    else face.viseme = "rest";
    draw(now);
    face.frame = requestAnimationFrame(loop);
  }

  function set(change) {
    if (change.state) face.state = change.state;
    if (change.emotion && EMOTIONS[change.emotion]) face.emotion = change.emotion;
    draw();
  }

  function stop() {
    face.track = null;
    face.word = null;
    face.viseme = "rest";
    if (face.state === "speaking") face.state = "idle";
    draw();
  }

  // Speak a reply when the phone doesn't report words: its shapes over the time it takes.
  function speak(text, durationMs) {
    face.state = "speaking";
    if (reducedMotion()) { face.viseme = 0.4; draw(); return; } // one still, half-open mouth
    const steps = trackFor(text);
    const total = steps.reduce((sum, s) => sum + s.weight, 0) || 1;
    face.track = { steps, unit: Math.max(40, (durationMs || 1000) / total), started: performance.now() };
    face.viseme = currentViseme(performance.now());
    draw();
  }

  // The phone says which word it is speaking now: that word's shapes, then wait for the next.
  function sayWord(word, ms) {
    face.state = "speaking";
    face.track = null; // real timing wins over the estimate from now on
    const steps = [...visemesFor(word), { v: "rest", weight: 0.3 }];
    const total = steps.reduce((sum, s) => sum + s.weight, 0) || 1;
    const duration = Math.max(120, Math.min(600, ms || String(word).length * 70));
    face.word = { steps, unit: duration / total, started: performance.now() };
    if (reducedMotion()) face.viseme = 0.4;
    draw();
  }

  function blinkSoon() {
    clearTimeout(face.blinkTimer);
    if (!face.stage || reducedMotion()) return;
    face.blinkTimer = setTimeout(() => {
      face.blink = 1; draw();
      setTimeout(() => {
        face.blink = 0; draw();
        if (Math.random() < 0.2) setTimeout(() => { face.blink = 1; draw(); setTimeout(() => { face.blink = 0; draw(); }, 110); }, 160);
        // Now and then a glance to one side.
        face.glance = Math.random() < 0.3 ? (Math.random() < 0.5 ? -3 : 3) : 0;
        blinkSoon();
      }, 130);
    }, 3000 + Math.random() * 3000);
  }

  // Mount the Live2D model when one is given and its runtime loaded; else the drawing.
  function mount(stage, options = {}) {
    face.stage = stage;
    face.mode = "svg";
    face.expression = null;
    if (options.live2d && window.live2dAvatar) {
      stage.innerHTML = `<div class="avatar-live2d" role="img" aria-label="Assistant"></div>`;
      if (window.live2dAvatar.mount(stage.firstElementChild, options.live2d.frame)) {
        face.mode = "live2d";
        stage.classList.add("live2d");
        face.started = performance.now();
        draw();
        cancelAnimationFrame(face.frame);
        face.frame = requestAnimationFrame(loop);
        return;
      }
    }
    stage.classList.remove("live2d");
    stage.innerHTML = SVG;
    face.started = performance.now();
    draw();
    blinkSoon();
    cancelAnimationFrame(face.frame);
    if (!reducedMotion()) face.frame = requestAnimationFrame(loop);
  }

  function unmount() {
    stop();
    cancelAnimationFrame(face.frame);
    clearTimeout(face.blinkTimer);
    if (face.mode === "live2d") window.live2dAvatar?.unmount();
    face.mode = "svg";
    face.stage?.classList.remove("live2d");
    if (face.stage) face.stage.innerHTML = "";
    face.stage = null;
  }

  // What the console tells it. "transcript": { waiting, last: { id, emotion } } after each
  // draw; "listening": { on }; "speak": { text, ms }; "word": { word, ms } while the phone
  // speaks; "speech": { state: "start" | "done" }; "stop".
  function avatarEvent(type, data = {}) {
    if (type === "transcript") {
      const last = data.last;
      if (last && last.id !== face.lastReply) {
        face.lastReply = last.id;
        if (last.emotion) face.emotion = last.emotion;
      }
      if (data.waiting && face.state === "idle") face.state = "thinking";
      else if (!data.waiting && face.state === "thinking") face.state = "idle";
      draw();
    } else if (type === "listening") {
      if (data.on) set({ state: "listening" });
      else if (face.state === "listening") set({ state: "idle" });
    } else if (type === "speak") speak(data.text, data.ms);
    else if (type === "word") sayWord(data.word, data.ms);
    else if (type === "speech") {
      if (data.state === "start") set({ state: "speaking" });
      else if (data.state === "done") stop();
    } else if (type === "stop") stop();
  }

  window.avatar = {
    set, speak, stop, pose, visemesFor, live2dParams, live2dMouthTarget, mount, unmount,
    state: () => ({ ...face, stage: undefined }),
  };
  window.avatarEvent = avatarEvent;
})();
