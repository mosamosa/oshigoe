# サードパーティーのライセンス

Oshigoe のライセンスは [Apache-2.0](LICENSE) です。ソフトウェアの依存物、モデル、フォントには、以下に記載する各配布元の条件が適用されます。起動スクリプトは依存パッケージと実行ファイルを各配布元から取得します。モデルは初回使用時に Hugging Face から、フォントは画面表示時に Google Fonts から取得します。

## Python パッケージ

[Python パッケージのライセンスと通知](licenses/python-packages.txt) には、2026 年 11 月 23 日に Apple Silicon Mac の起動環境（CPython 3.12.16）で確認した直接・間接依存のライセンスと通知を収録しています。各項目でパッケージのバージョンと元のファイル名を確認できます。実際に取得するバージョンとパッケージは、OS、CPU、起動時点によって異なります。直接依存は [requirements.txt](requirements.txt) を参照してください。

| パッケージ | ライセンスと配布元 |
| --- | --- |
| aiohttp | Apache-2.0。同梱の llhttp は MIT。[PyPI](https://pypi.org/project/aiohttp/) |
| aiortc | BSD-3-Clause。[ライセンス](https://github.com/aiortc/aiortc/blob/main/LICENSE) |
| ifaddr | MIT。[ライセンス](https://github.com/ifaddr/ifaddr/blob/master/LICENSE.txt) |
| inworld-voice-isolation | Apache-2.0。パッケージの `LICENSE` と `NOTICE` を収録しています。[`src/backend/media/parallel_scan.py`](src/backend/media/parallel_scan.py) の CUDA カーネルは、このパッケージの `scan.py` にある Metal カーネルを移植して変更したものです。[PyPI](https://pypi.org/project/inworld-voice-isolation/1.0.0/) |
| NumPy | BSD-3-Clause。本体と同梱コンポーネントの通知を収録しています。[ライセンス](https://numpy.org/doc/stable/license.html) |
| nvidia-ml-py | BSD-3-Clause。Windows でのみ取得します。wheel にライセンスファイルがないため、2026 年 11 月 24 日に Windows の起動環境で確認した 13.615.71 の `pynvml.py` 冒頭の著作権表示とライセンス文を収録しています。[PyPI](https://pypi.org/project/nvidia-ml-py/) |
| psutil | BSD-3-Clause。[ライセンス](https://github.com/giampaolo/psutil/blob/master/LICENSE) |
| yt-dlp | 本体は Unlicense。`yt-dlp[default]` が取得する Mutagen は GPL-2.0-or-later。[yt-dlp](https://github.com/yt-dlp/yt-dlp#licensing)・[Mutagen](https://github.com/quodlibet/mutagen/blob/main/COPYING) |
| PyAV | ソースコードは BSD-3-Clause。wheel に含まれる FFmpeg とコーデックには、ビルドに応じた条件が適用されます。[PyAV](https://github.com/PyAV-Org/PyAV)・[wheel のライセンスに関する上流の説明](https://github.com/PyAV-Org/PyAV/issues/2270) |

確認した PyAV 19.0.2 の macOS arm64 wheel では、`libavcodec` は LGPL-3.0-or-later と表示され、ビルド設定に x264 と x265 が含まれていました。Python パッケージの一覧には、wheel に含まれていた PyAV のライセンスを収録しています。wheel を再配布する場合は、同梱ライブラリの条件と対応するソースコードを配布物ごとに確認してください。[PyAV の FFmpeg ビルド](https://github.com/PyAV-Org/pyav-ffmpeg)・[FFmpeg の配布条件](https://ffmpeg.org/legal.html)

## モデル重み

アプリは Inworld Voice Isolator の `voice-isolator-v1-realtime` を使用します。モデル重みは Apache-2.0 です。[ライセンス原文](licenses/models/inworld-voice-isolator/LICENSE)・[NOTICE](licenses/models/inworld-voice-isolator/NOTICE)・[モデルの配布元](https://huggingface.co/inworld/voice-isolator/tree/main)

## 実行環境

| コンポーネント | ライセンスと配布元 |
| --- | --- |
| uv 0.13.9 | MIT または Apache-2.0。[MIT](licenses/runtime/uv-mit.txt)・[Apache-2.0](licenses/runtime/uv-apache.txt)・[配布元](https://github.com/astral-sh/uv/releases/tag/0.13.9) |
| CPython 3.12 | PSF ライセンスとその他の通知。確認した CPython 3.12.16 のライセンスを収録しています。ビルドには Python Build Standalone が使用されています。[CPython](licenses/runtime/cpython.txt)・[Python Build Standalone の MPL-2.0](licenses/runtime/python-build-standalone.txt)・[配布元](https://github.com/astral-sh/python-build-standalone) |
| Deno 2.9.8 | MIT。[ライセンス原文](licenses/runtime/deno.txt)・[配布元](https://github.com/denoland/deno/releases/tag/v2.9.8) |
| FFmpeg（Apple Silicon Mac）9.0.2 | GPL-3.0-or-later。[配布元](https://github.com/aagedal/ffmpeg-apple-silicon/releases/tag/v9.0.2-1) |
| FFmpeg（Windows x64）8.1.2 Essentials | 配布元の表記は GPLv3。[リリース](https://github.com/GyanD/codexffmpeg/releases/tag/8.1.2)・[配布元のライセンス説明](https://www.gyan.dev/ffmpeg/builds/#about) |

FFmpeg と関連ライブラリのライセンス原文は [GPLv3](licenses/runtime/gpl-3.0.txt)、[LGPLv3](licenses/runtime/lgpl-3.0.txt)、[LGPLv2.1](licenses/runtime/lgpl-2.1.txt) を参照してください。

## フォント

いずれも SIL Open Font License 1.1 です。

| フォント | ライセンス原文と配布元 |
| --- | --- |
| Inter | [ライセンス原文](licenses/fonts/inter.txt)・[配布元](https://github.com/google/fonts/tree/main/ofl/inter) |
| Noto Sans JP | [ライセンス原文](licenses/fonts/noto-sans-jp.txt)・[配布元](https://github.com/google/fonts/tree/main/ofl/notosansjp) |
| Space Grotesk | [ライセンス原文](licenses/fonts/space-grotesk.txt)・[配布元](https://github.com/google/fonts/tree/main/ofl/spacegrotesk) |

実行ファイルや wheel を同梱する場合は、その配布物に含まれるコンポーネントごとに、ライセンス表示、通知、対応するソースコードの提供条件を確認してください。
