# Changelog

## [0.10.0](https://github.com/gofrolist/retell-clone/compare/v0.9.0...v0.10.0) (2026-07-19)


### Features

* **dashboard:** Retell-style agent Webhook Settings (test, timeout, events) ([#121](https://github.com/gofrolist/retell-clone/issues/121)) ([6d77c92](https://github.com/gofrolist/retell-clone/commit/6d77c92b3863ecc5db83faeb395bf4c77a738515))
* **dashboard:** Retell-style LLM model picker dropdown ([#117](https://github.com/gofrolist/retell-clone/issues/117)) ([39b05c1](https://github.com/gofrolist/retell-clone/commit/39b05c141bafd5e95084a05f3dc7595b18e6155d))
* **dashboard:** working Test LLM chat in the agent editor ([#122](https://github.com/gofrolist/retell-clone/issues/122)) ([40ab965](https://github.com/gofrolist/retell-clone/commit/40ab965c7eb29b394c7e379ad4c45f7c54db0822))
* live Test Audio web calls in the agent editor ([#123](https://github.com/gofrolist/retell-clone/issues/123)) ([d646241](https://github.com/gofrolist/retell-clone/commit/d646241ecd311b4c7941e50e0e5dac58006f3a48))


### Bug Fixes

* **dashboard:** collapse Functions panel by default on agent page ([#120](https://github.com/gofrolist/retell-clone/issues/120)) ([df23af4](https://github.com/gofrolist/retell-clone/commit/df23af4255d8a929b5753503e77c2739a23bda65))
* **deps:** relax google-auth to stay compatible with google-genai ([#119](https://github.com/gofrolist/retell-clone/issues/119)) ([893ab00](https://github.com/gofrolist/retell-clone/commit/893ab008fee197e04f88d64e15b3cf2f23397b0d))

## [0.9.0](https://github.com/gofrolist/retell-clone/compare/v0.8.4...v0.9.0) (2026-07-18)


### Features

* **dashboard:** populate call-history Data and Detail Logs tabs ([#101](https://github.com/gofrolist/retell-clone/issues/101)) ([1a23066](https://github.com/gofrolist/retell-clone/commit/1a2306655cf23b18210abeaed921a392bb0df3a1))

## [0.8.4](https://github.com/gofrolist/retell-clone/compare/v0.8.3...v0.8.4) (2026-07-18)


### Bug Fixes

* **dashboard:** compute Gemini Live cost from real audio rates, not a flat $0.60 ([#99](https://github.com/gofrolist/retell-clone/issues/99)) ([a06d757](https://github.com/gofrolist/retell-clone/commit/a06d757c623145fb5669cdaab05139e1a98241c2))

## [0.8.3](https://github.com/gofrolist/retell-clone/compare/v0.8.2...v0.8.3) (2026-07-18)


### Bug Fixes

* **worker:** safety-net hangup for deferred end_call on Gemini Live ([#97](https://github.com/gofrolist/retell-clone/issues/97)) ([d8ea461](https://github.com/gofrolist/retell-clone/commit/d8ea461d9aac323216ef8c92ba1f5dee1432d4a1))

## [0.8.2](https://github.com/gofrolist/retell-clone/compare/v0.8.1...v0.8.2) (2026-07-18)


### Bug Fixes

* **worker:** flush SIP audio tail before hangup so goodbyes aren't clipped ([#95](https://github.com/gofrolist/retell-clone/issues/95)) ([c92013d](https://github.com/gofrolist/retell-clone/commit/c92013da36f02b8b39aa8f0d2b5aae0cefce0885))
* **worker:** greet via generate_reply on Gemini Live ([#94](https://github.com/gofrolist/retell-clone/issues/94)) ([0230c31](https://github.com/gofrolist/retell-clone/commit/0230c31d762a0677f75d7b12960a1274a5f1390a))

## [0.8.1](https://github.com/gofrolist/retell-clone/compare/v0.8.0...v0.8.1) (2026-07-18)


### Bug Fixes

* **dashboard:** hide inapplicable voice controls on the Gemini Live tab ([#92](https://github.com/gofrolist/retell-clone/issues/92)) ([eb58003](https://github.com/gofrolist/retell-clone/commit/eb58003875b652da6b119fdfc9f03bcd927140f2))

## [0.8.0](https://github.com/gofrolist/retell-clone/compare/v0.7.0...v0.8.0) (2026-07-17)


### Features

* add Gemini Live API realtime LLM + voice tab ([#89](https://github.com/gofrolist/retell-clone/issues/89)) ([0d41ec3](https://github.com/gofrolist/retell-clone/commit/0d41ec3f902f939de2a398fe819de8f45b61c802))


### Bug Fixes

* **dashboard:** keep Live model header on one row ([#91](https://github.com/gofrolist/retell-clone/issues/91)) ([8dd5264](https://github.com/gofrolist/retell-clone/commit/8dd5264dc72939754dacf068ba8ec4de0060d152))

## [0.7.0](https://github.com/gofrolist/retell-clone/compare/v0.6.0...v0.7.0) (2026-07-17)


### Features

* **dashboard:** editable default dynamic variables ([#86](https://github.com/gofrolist/retell-clone/issues/86)) ([04cc222](https://github.com/gofrolist/retell-clone/commit/04cc2220e0e8c34229d615096238057080eb82fd))
* **scripts:** transfer Retell default_dynamic_variables into Arhiteq ([#87](https://github.com/gofrolist/retell-clone/issues/87)) ([1e61f08](https://github.com/gofrolist/retell-clone/commit/1e61f0889e5d8bd36a74bce779b8a09b8ac57758))

## [0.6.0](https://github.com/gofrolist/retell-clone/compare/v0.5.1...v0.6.0) (2026-07-17)


### Features

* **dashboard:** show a type icon per function and align rows like Retell ([#83](https://github.com/gofrolist/retell-clone/issues/83)) ([9c17af1](https://github.com/gofrolist/retell-clone/commit/9c17af15bbb234869800d5ef3a74f1c58eff0119))


### Bug Fixes

* **analysis:** authenticate post-call analysis to Vertex via ADC ([#85](https://github.com/gofrolist/retell-clone/issues/85)) ([7f75b9e](https://github.com/gofrolist/retell-clone/commit/7f75b9e232964dbacb32f2bb28565227f85ba8b3))
* return 409 (not 500) when deleting an agent bound to a DID, keep CORS headers on errors ([#81](https://github.com/gofrolist/retell-clone/issues/81)) ([af5baa9](https://github.com/gofrolist/retell-clone/commit/af5baa9ac631e8e9bae12c757ecb36a512ac218c))

## [0.5.1](https://github.com/gofrolist/retell-clone/compare/v0.5.0...v0.5.1) (2026-07-16)


### Bug Fixes

* cap dev-server heap to survive Next 16.2.10 RSC leak ([#77](https://github.com/gofrolist/retell-clone/issues/77)) ([bf81eb8](https://github.com/gofrolist/retell-clone/commit/bf81eb80d69706e716602d9a8ae263d9e31c56b5))
* keep focus in modal inputs while typing ([#74](https://github.com/gofrolist/retell-clone/issues/74)) ([f296217](https://github.com/gofrolist/retell-clone/commit/f2962172f2306112c09cf76b5b36e1f3f22ef64a))
* match Retell panel widths on agent editor ([#75](https://github.com/gofrolist/retell-clone/issues/75)) ([97fd956](https://github.com/gofrolist/retell-clone/commit/97fd956adf49b11e6b2df8e4f33c8fe0e6ea8120))

## [0.5.0](https://github.com/gofrolist/retell-clone/compare/v0.4.0...v0.5.0) (2026-07-15)


### Features

* Retell default system dynamic variables ([#71](https://github.com/gofrolist/retell-clone/issues/71)) ([e92d21b](https://github.com/gofrolist/retell-clone/commit/e92d21b83daf85bb46d191aba5c7204827f097d9))
* Retell-style knowledge base UI with file upload ([#73](https://github.com/gofrolist/retell-clone/issues/73)) ([1825808](https://github.com/gofrolist/retell-clone/commit/182580877cd376d3fb76a865dddc4581c697eab4))

## [0.4.0](https://github.com/gofrolist/retell-clone/compare/v0.3.1...v0.4.0) (2026-07-14)


### Features

* live cost/latency/token estimates in agent details header ([#70](https://github.com/gofrolist/retell-clone/issues/70)) ([aa950ce](https://github.com/gofrolist/retell-clone/commit/aa950cea720d8152100e7d65d2175e24eed77b2b))
* working agent folders ([#68](https://github.com/gofrolist/retell-clone/issues/68)) ([dc4d1bc](https://github.com/gofrolist/retell-clone/commit/dc4d1bcfb0101574002d6fe0fa7a6a53b6e2fe54))

## [0.3.1](https://github.com/gofrolist/retell-clone/compare/v0.3.0...v0.3.1) (2026-07-14)


### Bug Fixes

* unblock stale sessions from member management, surface 403 reasons ([#66](https://github.com/gofrolist/retell-clone/issues/66)) ([bbee7e5](https://github.com/gofrolist/retell-clone/commit/bbee7e50642a03620d31651ba092f95ddec9b38d))

## [0.3.0](https://github.com/gofrolist/retell-clone/compare/v0.2.0...v0.3.0) (2026-07-14)


### Features

* drop voice modal top tabs, keep single provider bar ([#64](https://github.com/gofrolist/retell-clone/issues/64)) ([143bb6a](https://github.com/gofrolist/retell-clone/commit/143bb6a2fcf73b6dd86b1963c52ca91362cd0ff2))
* Retell-style voice selection modal ([#61](https://github.com/gofrolist/retell-clone/issues/61)) ([0857975](https://github.com/gofrolist/retell-clone/commit/08579754c11a8f4ed810f14dd5dfb9003f47b24b))
* workspace member invites ([#65](https://github.com/gofrolist/retell-clone/issues/65)) ([fc1d999](https://github.com/gofrolist/retell-clone/commit/fc1d99972e1997b729021830e42514a8c7b7f19c))

## [0.2.0](https://github.com/gofrolist/retell-clone/compare/v0.1.7...v0.2.0) (2026-07-13)


### Features

* automated release process (release-please + WIF deploy) ([#58](https://github.com/gofrolist/retell-clone/issues/58)) ([5f7a0d6](https://github.com/gofrolist/retell-clone/commit/5f7a0d6005d98acc6afdbef8ae5bbcf52942f504))
