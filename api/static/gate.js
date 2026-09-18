// Pre-generation quality gate (§2). Runs on the live preview, client-side, zero latency.
// Blocks the shutter until framing, pose and lighting are all green, because a bad photo
// costs $0.07 and produces a result that reads as "the model is bad" (§8-3).
//
// ponytail: thresholds below are eyeballed for a desk webcam at ~1.5m. They are the knob
// that will need turning for a kiosk with a fixed floor mark and a ring light — tune them
// against real captures rather than trusting these numbers.
const T = {
  visible: 0.6,        // landmark confidence to count as "in frame"
  shoulderSpan: 0.12,  // normalized; collapses when turned sideways
  noseOffset: 0.6,     // nose off-centre, as a fraction of half the shoulder span
  armGap: 0.035,       // wrist-to-hip horizontal gap; §5 wants arms 15-20 deg out
  uprightRatio: 0.15,  // hip->knee vertical drop needed to read as standing
  dark: 55, bright: 205, clipped: 0.28,
};

// MediaPipe Pose landmark indices.
const NOSE = 0, L_SH = 11, R_SH = 12, L_WR = 15, R_WR = 16,
      L_HIP = 23, R_HIP = 24, L_KNEE = 25, R_KNEE = 26, L_ANK = 27, R_ANK = 28;

let landmarker = null;

export async function loadGate() {
  const { FilesetResolver, PoseLandmarker } = await import(
    'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs');
  const fileset = await FilesetResolver.forVisionTasks(
    'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm');
  landmarker = await PoseLandmarker.createFromOptions(fileset, {
    baseOptions: {
      modelAssetPath: 'https://storage.googleapis.com/mediapipe-models/pose_landmarker/' +
                      'pose_landmarker_lite/float16/1/pose_landmarker_lite.task',
    },
    runningMode: 'VIDEO',
    numPoses: 1,
  });
  return landmarker;
}

function luminance(video) {
  // 64px sample is plenty to spot a blown-out window or a dark room.
  const c = luminance.c ||= Object.assign(document.createElement('canvas'), { width: 64, height: 64 });
  const ctx = c.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(video, 0, 0, 64, 64);
  const d = ctx.getImageData(0, 0, 64, 64).data;
  let sum = 0, clipped = 0;
  for (let i = 0; i < d.length; i += 4) {
    const y = 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
    sum += y;
    if (y > 250 || y < 5) clipped++;
  }
  const n = d.length / 4;
  return { mean: sum / n, clipped: clipped / n };
}

/**
 * @returns {{framing:Check, pose:Check, lighting:Check, ok:boolean}}
 *          Check = {ok:boolean, msg:string}
 */
export function check(video, needFullBody, timestampMs) {
  if (!landmarker) return null;
  const res = landmarker.detectForVideo(video, timestampMs);
  const lm = res.landmarks?.[0];

  const light = luminance(video);
  const lighting =
    light.clipped > T.clipped ? { ok: false, msg: 'Too much glare — turn away from the window' }
    : light.mean < T.dark     ? { ok: false, msg: 'Too dark — face a light source' }
    : light.mean > T.bright   ? { ok: false, msg: 'Overexposed — reduce backlight' }
    : { ok: true, msg: 'Lighting good' };

  if (!lm) {
    return { framing: { ok: false, msg: 'No person detected — step into frame' },
             pose: { ok: false, msg: '' }, lighting, ok: false };
  }

  const vis = i => (lm[i].visibility ?? 1) > T.visible;
  const need = [L_SH, R_SH, L_HIP, R_HIP, ...(needFullBody ? [L_KNEE, R_KNEE, L_ANK, R_ANK] : [])];
  const missing = need.filter(i => !vis(i));

  // §5: gate on garment category. A waist-up capture cannot wear trousers, so say
  // "step back" rather than generate something broken.
  const framing = missing.length === 0
    ? { ok: true, msg: needFullBody ? 'Full body in frame' : 'Upper body in frame' }
    : { ok: false, msg: missing.some(i => i >= L_KNEE)
        ? 'Step back — your legs and feet must be in frame for this garment'
        : 'Step back — head, shoulders and waist must all be visible' };

  const span = Math.abs(lm[L_SH].x - lm[R_SH].x);
  const midX = (lm[L_SH].x + lm[R_SH].x) / 2;
  const facing = span > T.shoulderSpan &&
                 Math.abs(lm[NOSE].x - midX) < (span / 2) * T.noseOffset;

  // Arms clear of the torso (§5) — this is what mangles hands when it fails (§8-4).
  const armsOut = Math.abs(lm[L_WR].x - lm[L_HIP].x) > T.armGap &&
                  Math.abs(lm[R_WR].x - lm[R_HIP].x) > T.armGap;
  const uncrossed = lm[L_WR].x > lm[R_WR].x;   // mirrored preview: left wrist sits right

  // Standing, not seated. Only checkable when the legs are in frame at all.
  const upright = !needFullBody ||
    ((lm[L_KNEE].y - lm[L_HIP].y) > T.uprightRatio && lm[L_ANK].y > lm[L_KNEE].y);

  const pose =
    !facing    ? { ok: false, msg: 'Face the camera straight on' }
    : !uncrossed ? { ok: false, msg: 'Uncross your arms' }
    : !armsOut ? { ok: false, msg: 'Move your arms away from your sides' }
    : !upright ? { ok: false, msg: 'Stand up — seated poses break the fit' }
    : { ok: true, msg: 'Pose good' };

  return { framing, pose, lighting, ok: framing.ok && pose.ok && lighting.ok };
}
