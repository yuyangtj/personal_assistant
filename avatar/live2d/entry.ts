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
let started = false;
let mounted = false;
let canvas: HTMLCanvasElement | null = null;

w.live2dAvatar = {
  /** Draw the model into `host` (a sized element), framed head and shoulders. */
  mount(host: HTMLElement, frame?: Frame): boolean {
    w.__live2dHost = host;
    if (frame) control().frame = frame;
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
    if (!LAppDelegate.getInstance().initialize()) return false;
    canvas = host.querySelector('canvas');
    LAppDelegate.getInstance().run();
    started = true;
    mounted = true;
    return true;
  },
  unmount(): void {
    if (!mounted) return;
    w.__live2dPaused = true;
    canvas?.remove();
    mounted = false;
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
