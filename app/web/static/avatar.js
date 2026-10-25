// The avatar: a small face drawn in SVG that shows what the assistant is doing (idle,
// listening, thinking, speaking) and how its last reply felt. Loaded only when switched on
// (see setAvatar in events.js). The rest of the console talks to it through avatarEvent;
// nothing else touches the drawing.
(() => {
  const EMOTIONS = {
    // brow lift (left, right), brow tilt in degrees (inner end up > 0), smile (mouth curve)
    Warm: { lift: [0, 0], tilt: 0, smile: 6 },
    Curious: { lift: [-1, -6], tilt: 0, smile: 2 },
    Excited: { lift: [-5, -5], tilt: 0, smile: 10 },
    Concerned: { lift: [-2, -2], tilt: 14, smile: -3 },
    Neutral: { lift: [0, 0], tilt: 0, smile: 1 },
  };
  const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // The shape for a moment, from what it's doing and feeling: a pure function, so a pose
  // can be checked without any timing. mouth: 0 (closed) to 1 (wide open); blink: 0 to 1.
  function pose(state, emotion, mouth = 0, blink = 0) {
    const feel = EMOTIONS[emotion] || EMOTIONS.Warm;
    return {
      browY: feel.lift.map((lift) => 37 + lift - (state === "listening" ? 2 : 0)),
      browTilt: feel.tilt,
      eyeRy: Math.max(0.8, (state === "listening" ? 9 : 7.5) * (1 - blink)),
      pupilDy: state === "thinking" ? -3 : 0,
      smile: state === "speaking" ? Math.min(feel.smile, 4) : feel.smile,
      open: state === "speaking" ? Math.max(0, Math.min(1, mouth)) : 0,
      ring: state === "listening",
      dots: state === "thinking",
    };
  }

  // How open the mouth is for each letter: vowels open it, spaces and punctuation close it.
  function mouthTrack(text) {
    return [...text].map((ch, i) => {
      if (/[aeiouyåäöAEIOUYÅÄÖ]/.test(ch)) return i % 2 ? 0.8 : 1;
      if (/[a-zA-Z0-9]/.test(ch)) return 0.35;
      return 0;
    });
  }

  const SVG = `<svg class="avatar-face" viewBox="0 0 120 120" role="img" aria-label="Assistant">
    <circle class="avatar-ring" cx="60" cy="62" r="54"></circle>
    <g class="avatar-body"><circle class="avatar-head" cx="60" cy="62" r="46"></circle>
      <circle class="avatar-cheek" cx="34" cy="72" r="6"></circle>
      <circle class="avatar-cheek" cx="86" cy="72" r="6"></circle>
      <path class="avatar-brow" data-side="0"></path>
      <path class="avatar-brow" data-side="1"></path>
      <ellipse class="avatar-eye" cx="44" cy="55" rx="6"></ellipse>
      <ellipse class="avatar-eye" cx="76" cy="55" rx="6"></ellipse>
      <path class="avatar-mouth"></path></g>
    <g class="avatar-dots"><circle cx="96" cy="22" r="3"></circle><circle cx="105" cy="16" r="3.5">
      </circle><circle cx="114" cy="9" r="4"></circle></g></svg>`;

  const face = { stage: null, state: "idle", emotion: "Warm", mouth: 0, blink: 0, lastReply: null,
                 speech: null, frame: null, blinkTimer: null };

  function draw() {
    if (!face.stage) return;
    const p = pose(face.state, face.emotion, face.mouth, face.blink);
    const svg = face.stage.querySelector("svg");
    svg.dataset.state = face.state;
    svg.dataset.emotion = face.emotion;
    svg.dataset.open = p.open.toFixed(2);
    svg.querySelectorAll(".avatar-brow").forEach((brow, side) => {
      // A gentle arch; the inner end (towards the nose) rises when concerned.
      const [outer, inner] = side ? [86, 68] : [34, 52];
      const y = p.browY[side];
      const lift = p.browTilt / 3;
      brow.setAttribute("d", `M${outer} ${y + 1} Q${(outer + inner) / 2} ${y - 3} ${inner} ${y - lift}`);
    });
    svg.querySelectorAll(".avatar-eye").forEach((eye) => {
      eye.setAttribute("ry", p.eyeRy);
      eye.setAttribute("cy", 55 + p.pupilDy);
    });
    // The lips: the top follows the smile; the bottom opens below it while speaking.
    const top = 79 + p.smile * 0.35;
    const bottom = 79 + p.smile * 1.1 + 1.5 + p.open * 16;
    svg.querySelector(".avatar-mouth").setAttribute(
      "d", `M42 79 Q60 ${top} 78 79 Q60 ${bottom} 42 79 Z`);
    svg.querySelector(".avatar-ring").classList.toggle("on", p.ring);
    svg.querySelector(".avatar-dots").classList.toggle("on", p.dots);
  }

  function set(change) {
    if (change.state) face.state = change.state;
    if (change.emotion && EMOTIONS[change.emotion]) face.emotion = change.emotion;
    draw();
  }

  function stop() {
    cancelAnimationFrame(face.frame);
    face.speech = null;
    face.mouth = 0;
    if (face.state === "speaking") face.state = "idle";
    draw();
  }

  // Speak a reply: the mouth follows its letters over the time the phone takes to say it.
  function speak(text, durationMs) {
    stop();
    face.state = "speaking";
    if (reducedMotion()) { face.mouth = 0.4; draw(); return; }
    const track = mouthTrack(text || "");
    const perLetter = Math.max(30, durationMs / Math.max(1, track.length));
    face.speech = { track, perLetter, started: performance.now() };
    draw(); // show "speaking" now, not a frame later
    const step = (now) => {
      if (!face.speech) return;
      // The first frame's time can be a little before started: never a negative letter.
      const index = Math.max(0, Math.floor((now - face.speech.started) / face.speech.perLetter));
      if (index >= face.speech.track.length) { stop(); return; }
      face.mouth += (face.speech.track[index] - face.mouth) * 0.5; // ease, no snapping
      draw();
      face.frame = requestAnimationFrame(step);
    };
    face.frame = requestAnimationFrame(step);
  }

  function blinkSoon() {
    clearTimeout(face.blinkTimer);
    if (!face.stage || reducedMotion()) return;
    face.blinkTimer = setTimeout(() => {
      face.blink = 1; draw();
      setTimeout(() => { face.blink = 0; draw(); blinkSoon(); }, 140);
    }, 3000 + Math.random() * 3000);
  }

  function mount(stage) {
    face.stage = stage;
    stage.innerHTML = SVG;
    draw();
    blinkSoon();
  }

  function unmount() {
    stop();
    clearTimeout(face.blinkTimer);
    if (face.stage) face.stage.innerHTML = "";
    face.stage = null;
  }

  // What the console tells it. "transcript": { waiting, last: { id, emotion } } after each
  // draw; "listening": true/false; "speak": { text, ms }; "stop".
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
    else if (type === "stop") stop();
  }

  window.avatar = { set, speak, stop, pose, mount, unmount, state: () => ({ ...face, stage: undefined }) };
  window.avatarEvent = avatarEvent;
})();
