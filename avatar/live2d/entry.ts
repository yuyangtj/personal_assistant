/**
 * The console's Live2D avatar: mounts the SDK sample's model renderer into the avatar
 * stage and lets the page drive it. Built by scripts/build_live2d.sh against the Live2D
 * Cubism SDK for Web you download yourself; nothing of Live2D's lives in this repository.
 */
import { LAppDelegate } from './lappdelegate';

type Params = Record<string, number>;
type Frame = { zoom: number; y: number };

const w = window as any;

function control(): { params: Params; frame: Frame } {
  w.__live2dControl = w.__live2dControl || { params: {}, frame: { zoom: 4, y: 0.7 } };
  return w.__live2dControl;
}

let mounted = false;

w.live2dAvatar = {
  /** Draw the model into `host` (a sized element), framed head and shoulders. */
  mount(host: HTMLElement, frame?: Frame): boolean {
    if (mounted) return true;
    w.__live2dHost = host;
    if (frame) control().frame = frame;
    if (!LAppDelegate.getInstance().initialize()) return false;
    LAppDelegate.getInstance().run();
    mounted = true;
    return true;
  },
  unmount(): void {
    if (!mounted) return;
    LAppDelegate.releaseInstance();
    w.__live2dHost?.querySelectorAll('canvas').forEach((c: HTMLCanvasElement) => c.remove());
    w.__live2dModel = undefined;
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
