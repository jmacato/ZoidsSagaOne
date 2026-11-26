.PHONY: verify quality c-complete native menus release

ROM ?= 0259 - Zoids Saga (J)(Independent).gba
DS_ROM ?= 0235 - Zoids Saga DS - Legend of Arcadia (Japan).nds
NATIVE_OUTPUT ?= /tmp/Zoids Saga Native.gba
VERSION := $(shell cat VERSION)

verify:
	python3 tools/verify.py --rom "$(ROM)"

quality:
	PYTHONDONTWRITEBYTECODE=1 python3 tools/c_quality.py

c-complete:
	PYTHONDONTWRITEBYTECODE=1 python3 tools/verify.py --rom "$(ROM)" --require-all-c

native:
	python3 tools/verify.py --rom "$(ROM)" --allow-mixed-c --output "$(NATIVE_OUTPUT)"

menus:
	python3 tools/build_menu_patch.py --rom "$(ROM)" --ds-rom "$(DS_ROM)" --output "Zoids Saga English.gba"

release: menus
	node tools/release_rom.mjs "Zoids Saga English.gba" "Zoids Saga English $(VERSION).gba" $(VERSION)
