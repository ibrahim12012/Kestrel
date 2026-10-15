# Linux-only screen previews of the installer. See preview.c.
#   make -C installer/setup/preview
#   make -C installer/setup/preview shots FONTS=/path/to/fonts

CC ?= cc
SRC = preview.c text_ft.c ../ui.c ../gfx.c ../util.c ../assets.c \
      ../xz/xz_dec_stream.c ../xz/xz_dec_lzma2.c ../xz/xz_dec_bcj.c ../xz/xz_crc32.c ../xz/xz_crc64.c
CFLAGS += -O2 -g -Wall -Wextra -Wno-unused-parameter -std=c11 $(shell pkg-config --cflags freetype2 libpng)
LDLIBS += $(shell pkg-config --libs freetype2 libpng) -lm

preview: $(SRC) ../*.h ../xz/*.h
	$(CC) $(CFLAGS) -o $@ $(SRC) $(LDLIBS)

clean:
	rm -f preview
