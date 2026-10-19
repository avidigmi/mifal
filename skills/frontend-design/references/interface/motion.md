# Motion craft

Read for changed animation or meaningful interaction timing. Adapted from
LottieFiles motion-design 1.0.0 and Impeccable animate; technical runtime
requirements and accessibility take precedence over stylistic heuristics.

## Direct the motion

Name the purpose: feedback, state change, spatial continuity, hierarchy, or an
authored creative moment. Identify the focal element, sequence and settled
state. Match intensity to the task and brand. Supporting layers are optional;
add one only when it improves the message. Ordinary controls can stay simple.

Use a small timing/easing vocabulary. Rough starting ranges are 100–150ms for
immediate feedback, 150–300ms for routine state changes and 300–500ms for larger
transitions. Authored storytelling can justify a different pace. Test reading
time and responsiveness rather than treating ranges as universal thresholds.

Natural deceleration often works for entrances; preserve velocity/continuity
when an interaction can be interrupted. Scroll-scrubbed movement may require
linear timing, including GSAP containerAnimation with `ease: "none"`. A fade
or instantaneous change can be the appropriate state/reduced-motion response.

One meaningful coordinated sequence beats decorative movement on every
section. Set a task-specific stagger budget; keep essential controls usable
without waiting for secondary motion. An interaction should remain comfortable
after repeated use, not merely impressive on the first viewing.

## Select and implement

Use CSS for simple effects. Choose the project's existing Motion/GSAP/native
API for dynamic state, gestures or sequences; read official docs matching its
installed version. Use one owner for each animated property and clock.

Keep content visible by default. Prefer appropriate composited properties,
bound costly filters/shadows/canvas work and measure on the target workload.
Pause nonessential loops offscreen/hidden; release observers, listeners,
timers and animation resources on unmount. A GPU path is not unlimited capacity.

## Preserve usability and prove behavior

Support reduced motion without erasing state feedback. Keep keyboard/focus
and touch paths usable. Provide appropriate pause/control for sustained motion
and preserve navigation escape from immersive scroll scenes.

Exercise interruption, rapid reversal/repeated input, hidden-tab return,
resize and remount where affected. Compare reduced-motion and normal modes.
Use relevant measurements for performance claims; label simulations and device
limits. A prototype scrubber or slow-motion control can aid review of a
complex sequence; it need not become permanent product UI.

Local adjustments replace upstream mandatory three-layer rules, universal
linear/opacity bans, fixed one-third constraints and contradictory stagger
limits. These changes preserve the requested runtime and accessible behavior.

<!-- Modified by Mifal: Mifal adapted selected Impeccable and LottieFiles guidance into this file. Upstream, revision and licence: third_party/provenance.json. -->
