# The Kleki gallery

Five models, five tabs, one browser. These paintings came out of a test of the
browser extension; painting is what gave the project its name.

<p align="center">
  <img src="assets/gallery/wall.svg" width="100%" alt="Five paintings hung salon-style on a slate gallery wall, each with the green owner tag of the agent that painted it: a fjord at golden hour, an isometric lighthouse island, a harmonic field, a portrait of a cartographer, and lemons with a blue jug.">
</p>

On 5 November 2026 five models each painted one commission in [Kleki](https://kleki.com), each in its own tab of one Chrome, only through `ca browser`: DOM pointer events, screenshots, and a green pen that carries the agent's name. The green tag under each painting is that name. The coordinator judged every result and sent weak ones back. The [run record](validation/2026-11-05-kleki-gallery.md) has the setup, counts, send-backs and every error.

<p align="center">
  <a href="assets/gallery/terra.jpg"><img src="assets/gallery/plate-terra.svg" width="100%" alt="Fjord at golden hour, painted in Kleki by GPT-6.1 Terra (reasoning effort xhigh): A fjord with ridges fading into blue haze, a low sun with a wide glow, a broken shimmer on the water and dark pines in front. 1,611 input actions in 64 minutes, 170 screenshots."></a>
</p>

**Fjord at golden hour** by GPT-6.1 Terra, reasoning effort xhigh: 1,611 input actions in 64 minutes. Kleki's linear and radial gradients make the sky and the sun's glow; separate ridge layers are softened with blur, and the reflections are mirrored from the ridge coordinates at 40% opacity. The first pass came back after 7.5 minutes as flat bands; it was sent back three times.

<p align="center">
  <a href="assets/gallery/astra.jpg"><img src="assets/gallery/plate-astra.svg" width="100%" alt="Isometric lighthouse island, painted in Kleki by GPT-6 Astra (reasoning effort high): A floating 2:1 isometric island with a striped lighthouse, a cottage, a path, round trees, a pier and boat, and a soft light beam. 4,762 input actions in 99 minutes, 231 screenshots."></a>
</p>

**Isometric lighthouse island** by GPT-6 Astra, reasoning effort high: 4,762 input actions in 99 minutes. Every vertex comes from one projection, P(x, y, z) = (600 + 40(x − y), 440 + 20(x + y) − z), painted on 12 layers. One short round darkened the right face, replaced scribbles with strata and laid the path. Astra titled it *The Last Light*.

<p align="center">
  <a href="assets/gallery/sol.jpg"><img src="assets/gallery/plate-sol.svg" width="100%" alt="Harmonic field, painted in Kleki by GPT-6.1 Sol (reasoning effort high): A generative harmonograph on deep ink: 202 scripted strokes, a palette mapped to the loop, varied width and opacity, one focal eye. 520 input actions in 47 minutes, 65 screenshots."></a>
</p>

**Harmonic field** by GPT-6.1 Sol, reasoning effort high: 520 input actions in 47 minutes. 202 scripted strokes on five layers: a soft underpainting, fine loops coloured along the curve, offset interference and gold glints at the cusp. The only piece accepted on its first delivery.

<p align="center">
  <a href="assets/gallery/opus.jpg"><img src="assets/gallery/plate-opus.svg" width="100%" alt="The cartographer, painted in Kleki by Claude Opus 5.5 (effort high): A three-quarter portrait of an invented elderly cartographer: brass spectacles, grey beard, knitted scarf, a pencil behind the ear, lamp light and a map behind. 13,049 input actions in 169 minutes, 112 screenshots."></a>
</p>

**The cartographer** by Claude Opus 5.5, effort high: 13,049 input actions in 169 minutes. The head was laid out by script first (eye line at half height, the far eye narrower), then modelled with soft strokes at 15–35% opacity; the beard, hair and eyebrows are thousands of 1–2 px strands. A second round added the knowing smile, the cool shadow side and the scarf's knot.

<p align="center">
  <a href="assets/gallery/sonnet.jpg"><img src="assets/gallery/plate-sonnet.svg" width="100%" alt="Lemons and blue jug, painted in Kleki by Claude Sonnet 5.5 (effort high): A Dutch golden-age study: a cobalt-glazed jug, three lemons (one cut), linen on a wooden table, a dark umber ground and one light from the upper left. 8,875 input actions in 107 minutes, 134 screenshots."></a>
</p>

**Lemons and blue jug** by Claude Sonnet 5.5, effort high: 8,875 input actions in 107 minutes. A small numpy lighting model (a cylinder for the jug, dimpled ellipsoids for the lemons) became rows of soft strokes, melted with Kleki's blur; speculars and the cut lemon's segments are placed by hand. This is the second version: a third, linen-only round came out worse and was discarded.


<p align="center">
  <img src="assets/gallery/progress-terra.gif" width="36%" alt="Time-lapse of Fjord at golden hour by GPT-6.1 Terra, from the blank canvas to the finished piece, with the green pen at work.">
  <img src="assets/gallery/progress-astra.gif" width="36%" alt="Time-lapse of Isometric lighthouse island by GPT-6 Astra, from the blank canvas to the finished piece, with the green pen at work.">
  <img src="assets/gallery/progress-sol.gif" width="36%" alt="Time-lapse of Harmonic field by GPT-6.1 Sol, from the blank canvas to the finished piece, with the green pen at work.">
  <img src="assets/gallery/progress-opus.gif" width="24%" alt="Time-lapse of The cartographer by Claude Opus 5.5, from the blank canvas to the finished piece, with the green pen at work.">
  <img src="assets/gallery/progress-sonnet.gif" width="36%" alt="Time-lapse of Lemons and blue jug by Claude Sonnet 5.5, from the blank canvas to the finished piece, with the green pen at work.">
</p>
<p align="center"><sub>Time-lapses from each tab, filmed with <code>ca browser screenshot</code> while the artists worked.</sub></p>

<p align="center">
  <img src="assets/gallery/pen-moment.gif" width="60%" alt="The green pen with its kleki-sonnet tag sweeps back and forth while the cobalt jug fills in, in 25 real captures from Sonnet's tab.">
  <br><sub>The pen at work: kleki-sonnet fills the cobalt jug, in 25 real captures from its tab.</sub>
</p>

<p align="center">
  <img src="assets/gallery/chart.svg" width="100%" alt="Input actions and minutes painting per model, as two bar panels: GPT-6.1 Terra 1,611 input actions and 64 minutes; GPT-6 Astra 4,762 input actions and 99 minutes; GPT-6.1 Sol 520 input actions and 47 minutes; Claude Opus 5.5 13,049 input actions and 169 minutes; Claude Sonnet 5.5 8,875 input actions and 107 minutes.">
</p>

## How it was run

Each artist got a commission, a shared brief and a window of its own, and was judged
and sent back until the piece held up. Input actions and minutes come from each
artist's own command log. The [run record](validation/2026-11-05-kleki-gallery.md)
covers the setup, every send-back, the pen footage, and every CA error and Kleki
quirk seen.

[Back to the README](../README.md)
