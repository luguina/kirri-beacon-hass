# Brand assets

The icon and logo Home Assistant shows for this integration — on the integration page, the
device page, and the Devices & Services list.

Since **Home Assistant 2026.3** a custom integration serves its own brand images out of a
`brand/` directory inside the integration, proxied at
`/api/brands/integration/kirri_beacon/<image>`, and those take priority over the
[brands CDN](https://brands.home-assistant.io)
([dev blog, 2026-02-24](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api/)).
Nothing is submitted anywhere and nothing is fetched over the network — these six files *are*
the mechanism. There is no PR to [`home-assistant/brands`](https://github.com/home-assistant/brands)
to open, and upstream no longer takes custom-integration submissions.

The filenames are load-bearing: Home Assistant serves this directory by name, so `icon.png` is
the only thing that can be the icon. Do not rename them.

## The files

| File | Size | Notes |
|---|---|---|
| `icon.png` | 256×256 | Square app mark, dark green on white — matches the vendor's own launcher icon |
| `icon@2x.png` | 512×512 | |
| `logo.png` | 508×256 | Wordmark, transparent background |
| `logo@2x.png` | 1016×512 | |
| `dark_logo.png` | 508×256 | Same wordmark knocked out to white, for Home Assistant's dark theme |
| `dark_logo@2x.png` | 1016×512 | |

The dark *logo* variants exist because the wordmark is near-black: on a dark theme the standard
one would be almost invisible. There is deliberately no `dark_icon.png` or `dark_icon@2x.png`,
though both are names Home Assistant accepts — the icon is a white tile that reads the same
either way, and HA's own fallback chain resolves those two requests to `icon.png` and
`icon@2x.png` *locally*. So all eight names it can ask for are answered from these six files, and
nothing ever falls through to the CDN.

## Provenance

All six are rendered from the vendor's own vector wordmark rather than upscaled from a bitmap,
so they are crisp at every size. Ink colour is the brand's `rgb(16, 43, 38)`.

The source is `assets/public/assets/images/logo-kirri.svg` **inside the Kirri Android app
package** — not a path in this repository, and not something you will find by looking here. The
APK and everything extracted from it are kept out of version control deliberately: vendor assets
and vendor code stay out (see
[Licence and trademarks](../../../README.md#licence-and-trademarks)). The rendered PNGs in this
directory are the only artefacts of it that are committed.

The mark is **Scent Australia Home's**, not this project's. It appears here for the reason a
manufacturer's logo appears anywhere in an integration list — to say *which* device this
controls. That is ordinary nominative use, and nothing here claims ownership.

## The one place they will not appear

The **HACS dashboard** draws its own tiles from `data-v2.hacs.xyz` rather than from Home
Assistant, so the entry there shows a grey placeholder no matter what is in this directory
([hacs/integration#5171](https://github.com/hacs/integration/issues/5171), open, with a fix
proposed upstream). It affects nothing in Home Assistant proper, and no workaround for it is
worth carrying.
