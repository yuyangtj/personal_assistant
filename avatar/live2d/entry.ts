/**
 * The console's Live2D avatar: mounts the SDK sample's model renderer into the avatar
 * stage and lets the page drive it. Built by scripts/build_live2d.sh against the Live2D
 * Cubism SDK for Web you download yourself; nothing of Live2D's lives in this repository.
 */
import { LAppDelegate } from './lappdelegate';
import { LAppPal } from './lapppal';

type Params = Record<string, number>;
type Frame = { zoom: number; y: number };

const w = window as any;

function control(): { params: Params; frame: Frame } {
  w.__live2dControl = w.__live2dControl || { params: {}, frame: { zoom: 4, y: 0.7 } };
  return w.__live2dControl;
}

// Live2D's sample can't be shut down and started again (its release leaves it broken), so
// it starts once and is then only paused: leaving takes its canvas off the page and stops
// drawing; coming back puts the same canvas into the new stage and draws again.
//
// The phone can take the GPU context away (screen off, app in the background, low
// memory). The sample can't rebuild its textures, so this bundle loads itself again: a
// fresh copy of the sample draws into a new canvas, and the old one stops.
const SRC = (document.currentScript as HTMLScriptElement | null)?.src;
let started = false;
let mounted = false;
let reloading = false;
let canvas: HTMLCanvasElement | null = null;

function reload(): void {
  if (!SRC || reloading) return;
  reloading = true;
  w.__live2dPaused = true; // the old copy stops drawing; the new one starts again
  canvas?.remove();
  canvas = null;
  w.__live2dModel = undefined;
  // Live2D's Core can't take a second copy of the sample, so it loads again first.
  const core = document.querySelector<HTMLScriptElement>('script[src*="live2dcubismcore"]')?.src;
  const load = (src: string, then: () => void) => {
    const script = document.createElement('script');
    script.src = src;
    script.onload = then;
    document.head.append(script);
  };
  // The new copy replaces window.live2dAvatar; it takes the stage if we still have one.
  const bundle = () =>
    load(SRC, () => {
      if (mounted && w.__live2dHost) w.live2dAvatar.mount(w.__live2dHost);
    });
  if (core) load(core, bundle);
  else bundle();
}

let waiting: ResizeObserver | null = null;

function start(host: HTMLElement): boolean {
  w.__live2dPaused = false;
  if (!LAppDelegate.getInstance().initialize()) return false;
  canvas = host.querySelector('canvas');
  canvas?.addEventListener('webglcontextlost', (event) => event.preventDefault());
  canvas?.addEventListener('webglcontextrestored', reload);
  LAppDelegate.getInstance().run();
  started = true;
  return true;
}

w.live2dAvatar = {
  /** Draw the model into `host` (a sized element), framed head and shoulders. */
  mount(host: HTMLElement, frame?: Frame): boolean {
    w.__live2dHost = host;
    if (frame) control().frame = frame;
    if (reloading) {
      mounted = true; // the new copy mounts here once loaded
      return true;
    }
    if (started && canvas) {
      if (canvas.parentElement !== host) host.appendChild(canvas);
      if (!mounted) {
        mounted = true;
        w.__live2dPaused = false;
        LAppPal.updateTime(); // no jump in the motion for the time it was away
        w.__live2dResume?.();
        w.__live2dResume = undefined;
      }
      return true;
    }
    mounted = true;
    // Live2D can't start at 0x0 (the page not laid out yet, or the stage behind another
    // view): it throws and never draws. Start once the stage has a size.
    if (!host.clientWidth || !host.clientHeight) {
      waiting?.disconnect();
      waiting = new ResizeObserver(() => {
        if (!host.clientWidth || !host.clientHeight) return;
        waiting?.disconnect();
        waiting = null;
        if (mounted && w.__live2dHost === host && !started) start(host);
      });
      waiting.observe(host);
      return true;
    }
    return start(host);
  },
  unmount(): void {
    waiting?.disconnect();
    waiting = null;
    if (!mounted) return;
    mounted = false;
    if (reloading) return;
    w.__live2dPaused = true;
    canvas?.remove();
  },
  /** Parameters set every frame, on top of the model's own motion (mouth, gaze, head). */
  setParams(params: Params): void {
    control().params = params;
  },
  setExpression(name: string): void {
    w.__live2dModel?.setExpression(name);
  },
  ready(): boolean {
    return Boolean(w.__live2dModel);
  },
};
