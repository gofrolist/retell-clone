# Changelog

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
