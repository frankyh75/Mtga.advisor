CC = clang
CFLAGS = -Wall -Wextra -O2 -std=c11
LDFLAGS = -framework CoreFoundation
TARGET = mtga-helper
BUILD_DIR = build

all: $(BUILD_DIR)/$(TARGET)

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(BUILD_DIR)/$(TARGET): main.c $(BUILD_DIR)
	$(CC) $(CFLAGS) -o $@ main.c $(LDFLAGS)

clean:
	rm -rf $(BUILD_DIR)
	rm -f /tmp/mtga-helper.sock

.PHONY: all clean
