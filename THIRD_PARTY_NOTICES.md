# Third-party notices

PTZLite's own code is proprietary (see [LICENSE](LICENSE)). The installer also includes the
following third-party software, each under its own licence. Those licences are not changed by
PTZLite's licence.

## go2rtc

The bundled `go2rtc.exe` streaming engine, and `static/video-rtc.js` and `static/video-stream.js`
(copied from go2rtc at runtime), are from https://github.com/AlexxIT/go2rtc.

```
MIT License

Copyright (c) 2022 Alexey Khit

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Python runtime and libraries

The Windows build bundles the Python interpreter and these libraries (versions as built). Each
library's full licence text is in its source repository.

| Component | Version | Licence | Source |
|---|---|---|---|
| Python | 3.14 | PSF-2.0 | https://www.python.org/ |
| FastAPI | 0.136.1 | MIT | https://github.com/fastapi/fastapi |
| Starlette | 1.0.0 | BSD-3-Clause | https://github.com/Kludex/starlette |
| Uvicorn | 0.46.0 | BSD-3-Clause | https://github.com/encode/uvicorn |
| Pydantic / pydantic-core | 2.13.3 / 2.46.3 | MIT | https://github.com/pydantic/pydantic |
| annotated-types | 0.7.0 | MIT | https://github.com/annotated-types/annotated-types |
| typing_extensions | 4.15.0 | PSF-2.0 | https://github.com/python/typing_extensions |
| AnyIO | 4.13.0 | MIT | https://github.com/agronholm/anyio |
| idna | 3.11 | BSD-3-Clause | https://github.com/kjd/idna |
| h11 | 0.16.0 | MIT | https://github.com/python-hyper/h11 |
| click | 8.3.1 | BSD-3-Clause | https://github.com/pallets/click |
| colorama | 0.4.6 | BSD-3-Clause | https://github.com/tartley/colorama |
| httptools | 0.7.1 | MIT | https://github.com/MagicStack/httptools |
| websockets | 16.0 | BSD-3-Clause | https://github.com/python-websockets/websockets |
| watchfiles | 1.1.1 | MIT | https://github.com/samuelcolvin/watchfiles |
| python-dotenv | 1.2.2 | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| aiohttp | 3.13.3 | Apache-2.0 AND MIT | https://github.com/aio-libs/aiohttp |
| aiohappyeyeballs | 2.6.1 | PSF-2.0 | https://github.com/aio-libs/aiohappyeyeballs |
| aiosignal | 1.4.0 | Apache-2.0 | https://github.com/aio-libs/aiosignal |
| attrs | 25.4.0 | MIT | https://github.com/python-attrs/attrs |
| frozenlist | 1.8.0 | Apache-2.0 | https://github.com/aio-libs/frozenlist |
| multidict | 6.7.1 | Apache-2.0 | https://github.com/aio-libs/multidict |
| propcache | 0.4.1 | Apache-2.0 | https://github.com/aio-libs/propcache |
| yarl | 1.22.0 | Apache-2.0 | https://github.com/aio-libs/yarl |
| PyYAML | 6.0.3 | MIT | https://github.com/yaml/pyyaml |
| pystray | 0.19.5 | LGPL-3.0 | https://github.com/moses-palmer/pystray |
| six | 1.17.0 | MIT | https://github.com/benjaminp/six |
| Pillow | 12.2.0 | MIT-CMU | https://github.com/python-pillow/Pillow |

**pystray (LGPL-3.0):** pystray is used unmodified. Its complete source code is available at the
link above, and you may replace the bundled copy with your own build of the same library.
