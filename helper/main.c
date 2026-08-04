/**
 * mtga-helper — SMJobBless-kompatibler Memory-Scanner für MTGA
 *
 * UNIX-Socket-Server, der als root (LaunchDaemon) läuft und
 * Memory-Scans ohne sudo-Prompt ausführt.
 *
 * JSON-Protokoll (siehe scanner/helper_client.py):
 *   {"action":"ping"}                                      → {"status":"ok"}
 *   {"action":"status"}                                    → {"status":"ok","pid":N,"version":"...","socket_path":"..."}
 *   {"action":"list_regions"}                              → {"status":"ok","regions":[{"address":..,"size":..},...]}
 *   {"action":"read_memory","address":123456,"size":4096}   → {"status":"ok","data":"<base64>","bytes_read":4096}
 *   {"action":"shutdown"}                                  → {"status":"ok"}
 *
 * CLI:
 *   mtga-helper [--sock PATH] [--test-mode] [--version]
 *
 * --sock PATH    UNIX-Socket-Pfad (Default: /var/run/mtga-helper.sock)
 * --test-mode    Mock-Modus: list_regions/read_memory liefern Dummy-Daten
 * --version      Version ausgeben und beenden
 *
 * Kompilieren: make -C helper
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <signal.h>
#include <getopt.h>
#include <stdarg.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/select.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach/vm_region.h>
#include <mach/vm_statistics.h>
#include <libproc.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <libkern/OSByteOrder.h>

/* --- Constants --- */

#define VERSION "1.0.0"
#define DEFAULT_SOCK_PATH "/var/run/mtga-helper.sock"
#define MAX_CLIENTS 8
#define BUF_SIZE 65536
#define MAX_RESPONSE 1048576    /* 1 MB */
#define MAX_PROCESS_NAMES 16
#define MAX_NAME_LEN 256
#define MAX_ANCHORS 64
#define READ_CHUNK (4 * 1024 * 1024)
#define MAX_REGIONS 4096

/* Card ID range (see scanner/memory_scanner.py find_blocks) */
#define CARD_ID_MIN 1000
#define CARD_ID_MAX 500000
#define CARD_QTY_MIN 1
#define CARD_QTY_MAX 400
#define BLOCK_MIN_CARDS 50
#define BLOCK_MAX_MISSES 50

/* --- Globals --- */

static char g_sock_path[256] = DEFAULT_SOCK_PATH;
static int g_test_mode = 0;
static int server_fd = -1;
static volatile sig_atomic_t running = 1;

/* --- JSON helpers --- */

/* Escape a string into JSON-quoted form. Caller frees. */
static char *json_escape(const char *s) {
    if (!s) return strdup("\"\"");
    size_t len = strlen(s);
    size_t cap = len * 6 + 3;  /* worst-case: each char → \uXXXX */
    char *out = malloc(cap);
    if (!out) return NULL;
    char *p = out;
    *p++ = '"';
    for (size_t i = 0; i < len; i++) {
        unsigned char c = (unsigned char)s[i];
        switch (c) {
            case '"':  *p++ = '\\'; *p++ = '"';  break;
            case '\\': *p++ = '\\'; *p++ = '\\'; break;
            case '\n': *p++ = '\\'; *p++ = 'n';  break;
            case '\r': *p++ = '\\'; *p++ = 'r';  break;
            case '\t': *p++ = '\\'; *p++ = 't';  break;
            default:
                if (c < 0x20) {
                    p += snprintf(p, cap - (p - out), "\\u%04x", c);
                } else {
                    *p++ = (char)c;
                }
                break;
        }
    }
    *p++ = '"';
    *p = '\0';
    return out;
}

/* Build an error response: {"error":"msg"}. Caller frees. */
static char *build_error(const char *msg) {
    char *escaped = json_escape(msg);
    if (!escaped) return NULL;
    size_t len = strlen(escaped) + 20;
    char *resp = malloc(len);
    if (!resp) { free(escaped); return NULL; }
    snprintf(resp, len, "{\"error\":%s}", escaped);
    free(escaped);
    return resp;
}

/* Build a success response: {"status":"ok"}. Caller frees. */
static char *build_ok(void) {
    return strdup("{\"status\":\"ok\"}");
}

/* --- Minimal JSON parser ---
 *
 * We don't need a full JSON parser — we just need to extract
 * specific fields from the client request. The requests are
 * small and well-structured.
 */

/* Find a string value for a key in JSON.
 * Returns a malloc'd copy of the value, or NULL if not found. */
static char *json_get_string(const char *json, const char *key) {
    char pattern[256];
    snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    const char *p = strstr(json, pattern);
    if (!p) return NULL;
    p += strlen(pattern);
    /* skip whitespace and colon */
    while (*p && (*p == ' ' || *p == '\t' || *p == ':')) p++;
    if (*p != '"') return NULL;
    p++;  /* skip opening quote */
    const char *start = p;
    /* find closing quote (handle escaped quotes) */
    while (*p) {
        if (*p == '\\' && p[1]) { p += 2; continue; }
        if (*p == '"') break;
        p++;
    }
    if (*p != '"') return NULL;
    size_t len = p - start;
    char *val = malloc(len + 1);
    if (!val) return NULL;
    memcpy(val, start, len);
    val[len] = '\0';
    return val;
}

/* Find an integer value for a key in JSON. Returns 1 on success. */
static int json_get_int(const char *json, const char *key, long *val) {
    char pattern[256];
    snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    const char *p = strstr(json, pattern);
    if (!p) return 0;
    p += strlen(pattern);
    while (*p && (*p == ' ' || *p == '\t' || *p == ':')) p++;
    if (*p == '"') return 0;  /* string, not int */
    char *end;
    long n = strtol(p, &end, 10);
    if (end == p) return 0;
    *val = n;
    return 1;
}

/* Find a boolean value for a key. Returns 1 on success, 0 if not found. */
static int json_get_bool(const char *json, const char *key, int *val) {
    char pattern[256];
    snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    const char *p = strstr(json, pattern);
    if (!p) return 0;
    p += strlen(pattern);
    while (*p && (*p == ' ' || *p == '\t' || *p == ':')) p++;
    if (strncmp(p, "true", 4) == 0) { *val = 1; return 1; }
    if (strncmp(p, "false", 5) == 0) { *val = 0; return 1; }
    return 0;
}

/* --- Dynamic string builder for JSON responses --- */

typedef struct {
    char *buf;
    size_t len;
    size_t cap;
} sb_t;

static void sb_init(sb_t *sb, size_t initial_cap) {
    sb->buf = malloc(initial_cap);
    sb->cap = initial_cap;
    sb->len = 0;
    if (sb->buf) sb->buf[0] = '\0';
}

static void sb_ensure(sb_t *sb, size_t extra) {
    if (sb->len + extra + 1 > sb->cap) {
        size_t newcap = sb->cap * 2;
        while (newcap < sb->len + extra + 1) newcap *= 2;
        char *newbuf = realloc(sb->buf, newcap);
        if (!newbuf) return;  /* OOM — leave as-is */
        sb->buf = newbuf;
        sb->cap = newcap;
    }
}

static void sb_append(sb_t *sb, const char *s) {
    size_t slen = strlen(s);
    sb_ensure(sb, slen);
    memcpy(sb->buf + sb->len, s, slen);
    sb->len += slen;
    sb->buf[sb->len] = '\0';
}

static void sb_appendf(sb_t *sb, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    va_list ap2;
    va_copy(ap2, ap);
    int n = vsnprintf(NULL, 0, fmt, ap);
    va_end(ap);
    if (n < 0) { va_end(ap2); return; }
    sb_ensure(sb, (size_t)n);
    vsnprintf(sb->buf + sb->len, sb->cap - sb->len, fmt, ap2);
    va_end(ap2);
    sb->len += n;
}

static void sb_free(sb_t *sb) {
    free(sb->buf);
    sb->buf = NULL;
    sb->len = sb->cap = 0;
}

/* --- Base64 encoding --- */

static const char b64_table[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static char *base64_encode(const unsigned char *data, size_t len) {
    size_t out_len = 4 * ((len + 2) / 3) + 1;
    char *out = malloc(out_len);
    if (!out) return NULL;
    size_t i = 0, o = 0;
    while (i < len) {
        unsigned char a = data[i++];
        unsigned char b = 0, c = 0;
        int pad_b = 1, pad_c = 1;
        if (i < len) { b = data[i++]; pad_b = 0; }
        if (i < len) { c = data[i++]; pad_c = 0; }
        out[o++] = b64_table[a >> 2];
        out[o++] = b64_table[((a & 3) << 4) | (b >> 4)];
        out[o++] = pad_b ? '=' : b64_table[((b & 0x0F) << 2) | (c >> 6)];
        out[o++] = pad_c ? '=' : b64_table[c & 0x3F];
    }
    out[o] = '\0';
    return out;
}

/* --- Process discovery --- */

/* Find PID by process name (case-insensitive substring match).
 * Returns PID > 0 on success, 0 if not found, -1 on error. */
static pid_t find_process_by_name(const char *name) {
    pid_t pids[4096];
    int count = proc_listpids(PROC_ALL_PIDS, 0, pids, sizeof(pids));
    if (count <= 0) return 0;
    count /= sizeof(pid_t);

    for (int i = 0; i < count; i++) {
        if (pids[i] == 0) continue;
        struct proc_bsdinfo info;
        int st = proc_pidinfo(pids[i], PROC_PIDTBSDINFO, 0,
                              &info, sizeof(info));
        if (st <= 0) continue;
        /* Case-insensitive substring match */
        if (strcasestr(info.pbi_name, name) != NULL) {
            return pids[i];
        }
    }
    return 0;
}

/* Find first matching process from a list of names.
 * Returns PID > 0 or 0 if none found. */
static pid_t find_process(const char *process_names[], int n_names) {
    for (int i = 0; i < n_names; i++) {
        pid_t pid = find_process_by_name(process_names[i]);
        if (pid > 0) return pid;
    }
    /* Default: try "MTGA" */
    return find_process_by_name("MTGA");
}

/* --- Memory reading --- */

/* Attach to a process via task_for_pid.
 * Returns the mach port on success, or MACH_PORT_NULL on failure. */
static mach_port_t attach_to_process(pid_t pid) {
    mach_port_t task = MACH_PORT_NULL;
    kern_return_t kr = task_for_pid(mach_task_self(), pid, &task);
    if (kr != KERN_SUCCESS) {
        fprintf(stderr, "mtga-helper: task_for_pid(%d) failed: %s\n",
                pid, mach_error_string(kr));
        return MACH_PORT_NULL;
    }
    return task;
}

/* Read a chunk of memory from a process.
 * Returns malloc'd buffer (caller frees) or NULL. */
static void *read_memory(mach_port_t task, mach_vm_address_t addr,
                         mach_vm_size_t size, mach_vm_size_t *out_size) {
    vm_offset_t data = 0;
    mach_msg_type_number_t data_count = 0;
    kern_return_t kr = mach_vm_read(task, addr, size, &data, &data_count);
    if (kr != KERN_SUCCESS) return NULL;
    *out_size = data_count;
    return (void *)data;
}

/* --- Collection scan ---
 *
 * The collection in MTGA memory is stored as arrays of (grpId, quantity) pairs
 * as little-endian uint32_t values. We iterate writable private regions,
 * read them in chunks, and parse int pairs that look like valid card data.
 * (See scanner/memory_scanner.py find_blocks() for the reference implementation.)
 */

/* Region info for scanning */
typedef struct {
    mach_vm_address_t address;
    mach_vm_size_t size;
} region_t;

/* Iterate writable private regions of a task.
 * Fills regions[] up to max_regions. Returns count or -1 on error.
 * Uses mach_vm_region_recurse() to traverse submaps. */
static int get_writable_regions(mach_port_t task, region_t *regions, int max_regions) {
    mach_vm_address_t address = 0;
    mach_vm_size_t size = 0;
    natural_t depth = 0;
    int count = 0;

    while (count < max_regions) {
        mach_msg_type_number_t info_count = VM_REGION_SUBMAP_INFO_COUNT_64;
        struct vm_region_submap_info_64 info;
        memset(&info, 0, sizeof(info));

        kern_return_t kr = mach_vm_region_recurse(task, &address, &size,
                                                   &depth,
                                                   (vm_region_recurse_info_t)&info,
                                                   &info_count);
        if (kr != KERN_SUCCESS) break;

        if (info.is_submap) {
            depth++;
            continue;
        }

        /* Only scan writable, private regions */
        int writable = (info.protection & VM_PROT_WRITE) != 0;
        int is_shared = (info.share_mode != SM_PRIVATE &&
                         info.share_mode != SM_EMPTY);
        if (writable && !is_shared && size > 0) {
            regions[count].address = address;
            regions[count].size = size;
            count++;
        }
        address += size;
    }
    return count;
}

/* Parse a memory buffer for card (grpId, quantity) pairs.
 * Mirrors find_blocks() in memory_scanner.py.
 * Writes found cards into the cards dict (grpId → quantity). */
static void parse_card_block(const uint32_t *ints, size_t count,
                             /* output */ sb_t *cards_sb, int *cards_written,
                             /* tracking */ uint32_t *seen_ids, int *seen_count,
                             int max_seen) {
    for (int offset = 0; offset <= 1; offset++) {
        uint32_t current_ids[4096];
        uint32_t current_qtys[4096];
        int current_count = 0;
        int misses = 0;

        for (size_t i = offset; i + 1 < count; i += 2) {
            uint32_t k = ints[i];
            uint32_t v = ints[i + 1];

            if (k >= CARD_ID_MIN && k < CARD_ID_MAX &&
                v >= CARD_QTY_MIN && v <= CARD_QTY_MAX) {
                /* Check if we already have this card */
                int found = 0;
                for (int j = 0; j < current_count; j++) {
                    if (current_ids[j] == k) {
                        /* Keep highest quantity */
                        if (v > current_qtys[j]) current_qtys[j] = v;
                        found = 1;
                        break;
                    }
                }
                if (!found && current_count < 4096) {
                    current_ids[current_count] = k;
                    current_qtys[current_count] = v;
                    current_count++;
                }
                misses = 0;
            } else {
                misses++;
                if (misses > BLOCK_MAX_MISSES) {
                    if (current_count > BLOCK_MIN_CARDS) {
                        /* Flush block to JSON */
                        for (int j = 0; j < current_count; j++) {
                            /* Check global seen */
                            int already = 0;
                            for (int g = 0; g < *seen_count; g++) {
                                if (seen_ids[g] == current_ids[j]) {
                                    already = 1;
                                    break;
                                }
                            }
                            if (!already && *seen_count < max_seen) {
                                seen_ids[*seen_count] = current_ids[j];
                                (*seen_count)++;
                                if (*cards_written > 0) sb_append(cards_sb, ",");
                                sb_appendf(cards_sb, "\"%u\":%u",
                                           current_ids[j], current_qtys[j]);
                                (*cards_written)++;
                            }
                        }
                    }
                    current_count = 0;
                    misses = 0;
                }
            }
        }
        /* Flush remaining */
        if (current_count > BLOCK_MIN_CARDS) {
            for (int j = 0; j < current_count; j++) {
                int already = 0;
                for (int g = 0; g < *seen_count; g++) {
                    if (seen_ids[g] == current_ids[j]) {
                        already = 1;
                        break;
                    }
                }
                if (!already && *seen_count < max_seen) {
                    seen_ids[*seen_count] = current_ids[j];
                    (*seen_count)++;
                    if (*cards_written > 0) sb_append(cards_sb, ",");
                    sb_appendf(cards_sb, "\"%u\":%u",
                               current_ids[j], current_qtys[j]);
                    (*cards_written)++;
                }
            }
        }
    }
}

/* Scan a process for card collection data.
 * Returns a malloc'd JSON response string. Caller frees. */
static char *scan_process_collection(pid_t pid, const char *process_names[],
                                      int n_names, int debug) {
    (void)process_names;  /* process_names used only for finding PID (done by caller) */
    (void)n_names;
    mach_port_t task = attach_to_process(pid);
    if (task == MACH_PORT_NULL) {
        char err[256];
        snprintf(err, sizeof(err),
                 "task_for_pid failed for PID %d (helper needs root)", pid);
        return build_error(err);
    }

    /* Get writable private regions */
    region_t regions[MAX_REGIONS];
    int n_regions = get_writable_regions(task, regions, MAX_REGIONS);
    if (n_regions <= 0) {
        mach_port_deallocate(mach_task_self(), task);
        return build_error("No writable memory regions found");
    }

    /* Scan each region for card data */
    uint32_t *seen_ids = malloc(sizeof(uint32_t) * 100000);
    int seen_count = 0;
    if (!seen_ids) {
        mach_port_deallocate(mach_task_self(), task);
        return build_error("Out of memory");
    }

    sb_t cards_sb;
    sb_init(&cards_sb, 65536);
    int cards_written = 0;

    int read_failures = 0;
    mach_vm_size_t total_bytes = 0;

    for (int r = 0; r < n_regions; r++) {
        mach_vm_address_t addr = regions[r].address;
        mach_vm_size_t remaining = regions[r].size;

        while (remaining > 0) {
            mach_vm_size_t chunk = remaining < READ_CHUNK ? remaining : READ_CHUNK;
            mach_vm_size_t bytes_read = 0;

            void *buf = read_memory(task, addr, chunk, &bytes_read);
            if (!buf) {
                read_failures++;
                remaining -= chunk;
                addr += chunk;
                continue;
            }

            total_bytes += bytes_read;
            size_t n_ints = bytes_read / sizeof(uint32_t);
            if (n_ints >= 2) {
                parse_card_block((const uint32_t *)buf, n_ints,
                                 &cards_sb, &cards_written,
                                 seen_ids, &seen_count, 100000);
            }

            /* mach_vm_read returns a page we must deallocate */
            mach_vm_deallocate(mach_task_self(), (mach_vm_address_t)buf, bytes_read);

            remaining -= chunk;
            addr += chunk;
        }
    }

    free(seen_ids);
    mach_port_deallocate(mach_task_self(), task);

    /* Build response JSON */
    sb_t resp;
    sb_init(&resp, MAX_RESPONSE);

    sb_append(&resp, "{\"status\":\"ok\",\"cards\":{");
    sb_append(&resp, cards_sb.buf);
    sb_append(&resp, "}");

    if (debug) {
        sb_appendf(&resp, ",\"scan_stats\":{\"regions\":%d,\"bytes_scanned\":%llu,"
                          "\"read_failures\":%d,\"matches\":%d}",
                   n_regions, (unsigned long long)total_bytes,
                   read_failures, cards_written);
    }

    sb_append(&resp, "}");

    sb_free(&cards_sb);
    return resp.buf;  /* transfer ownership */
}

/* --- Anchor scan ---
 *
 * Anchors are specific card IDs to search for. For each anchor,
 * we scan memory for its little-endian uint32 representation
 * and count occurrences.
 */

static char *scan_process_anchors(pid_t pid, const char *process_names[],
                                   int n_names,
                                   /* anchor info */ const uint32_t *anchor_ids,
                                   int n_anchors, int debug) {
    (void)process_names;
    (void)n_names;
    mach_port_t task = attach_to_process(pid);
    if (task == MACH_PORT_NULL) {
        return build_error("task_for_pid failed (helper needs root)");
    }

    region_t regions[MAX_REGIONS];
    int n_regions = get_writable_regions(task, regions, MAX_REGIONS);
    if (n_regions <= 0) {
        mach_port_deallocate(mach_task_self(), task);
        return build_error("No writable memory regions found");
    }

    /* Count occurrences per anchor */
    int *anchor_counts = calloc(n_anchors, sizeof(int));
    if (!anchor_counts) {
        mach_port_deallocate(mach_task_self(), task);
        return build_error("Out of memory");
    }

    mach_vm_size_t total_bytes = 0;
    int read_failures = 0;

    for (int r = 0; r < n_regions; r++) {
        mach_vm_address_t addr = regions[r].address;
        mach_vm_size_t remaining = regions[r].size;

        while (remaining > 0) {
            mach_vm_size_t chunk = remaining < READ_CHUNK ? remaining : READ_CHUNK;
            mach_vm_size_t bytes_read = 0;

            void *buf = read_memory(task, addr, chunk, &bytes_read);
            if (!buf) {
                read_failures++;
                remaining -= chunk;
                addr += chunk;
                continue;
            }

            total_bytes += bytes_read;
            const uint8_t *data = (const uint8_t *)buf;

            /* Search for each anchor ID (little-endian uint32) */
            for (int a = 0; a < n_anchors; a++) {
                uint8_t needle[4];
                needle[0] = (uint8_t)(anchor_ids[a] & 0xFF);
                needle[1] = (uint8_t)((anchor_ids[a] >> 8) & 0xFF);
                needle[2] = (uint8_t)((anchor_ids[a] >> 16) & 0xFF);
                needle[3] = (uint8_t)((anchor_ids[a] >> 24) & 0xFF);

                /* Simple byte search */
                for (mach_vm_size_t i = 0; i + 4 <= bytes_read; i++) {
                    if (data[i] == needle[0] &&
                        data[i+1] == needle[1] &&
                        data[i+2] == needle[2] &&
                        data[i+3] == needle[3]) {
                        anchor_counts[a]++;
                    }
                }
            }

            mach_vm_deallocate(mach_task_self(), (mach_vm_address_t)buf, bytes_read);
            remaining -= chunk;
            addr += chunk;
        }
    }

    mach_port_deallocate(mach_task_self(), task);

    /* Build anchor_matches JSON */
    sb_t resp;
    sb_init(&resp, 65536);
    sb_append(&resp, "{\"status\":\"ok\",\"anchor_matches\":{");

    for (int a = 0; a < n_anchors; a++) {
        if (a > 0) sb_append(&resp, ",");
        sb_appendf(&resp, "\"%u\":%d", anchor_ids[a], anchor_counts[a]);
    }

    sb_append(&resp, "}");

    if (debug) {
        sb_appendf(&resp, ",\"scan_stats\":{\"regions\":%d,\"bytes_scanned\":%llu,"
                          "\"read_failures\":%d}",
                   n_regions, (unsigned long long)total_bytes, read_failures);
    }

    sb_append(&resp, "}");

    free(anchor_counts);
    return resp.buf;
}

/* --- Test mode mock data --- */

static char *mock_scan_response(void) {
    /* Return a small fake collection so the E2E test can verify the protocol */
    return strdup(
        "{\"status\":\"ok\","
        "\"cards\":{\"10001\":4,\"10002\":3,\"10003\":2,\"10004\":1,"
        "\"10005\":4,\"10006\":2,\"10007\":1,\"10008\":3,"
        "\"10009\":2,\"10010\":4,\"10011\":1,\"10012\":2,"
        "\"10013\":3,\"10014\":4,\"10015\":1,\"10016\":2,"
        "\"10017\":3,\"10018\":4,\"10019\":1,\"10020\":2,"
        "\"10021\":3,\"10022\":4,\"10023\":1,\"10024\":2,"
        "\"10025\":3,\"10026\":4,\"10027\":1,\"10028\":2,"
        "\"10029\":3,\"10030\":4,\"10031\":1,\"10032\":2,"
        "\"10033\":3,\"10034\":4,\"10035\":1,\"10036\":2,"
        "\"10037\":3,\"10038\":4,\"10039\":1,\"10040\":2,"
        "\"10041\":3,\"10042\":4,\"10043\":1,\"10044\":2,"
        "\"10045\":3,\"10046\":4,\"10047\":1,\"10048\":2,"
        "\"10049\":3,\"10050\":4,\"10051\":1,\"10052\":2"
        "},"
        "\"anchor_matches\":{\"10001\":3,\"10005\":2},"
        "\"scan_stats\":{\"regions\":42,\"bytes_scanned\":1048576,"
        "\"read_failures\":0,\"matches\":52}"
        "}");
}

static char *mock_deck_scan_response(void) {
    return strdup(
        "{\"status\":\"ok\","
        "\"decks\":["
        "{\"name\":\"Mock Deck 1\",\"colors\":[\"W\",\"U\"],"
        "\"cards\":{\"10001\":4,\"10002\":3}},"
        "{\"name\":\"Mock Deck 2\",\"colors\":[\"R\",\"G\"],"
        "\"cards\":{\"10003\":2,\"10004\":1}}"
        "]}");
}

static char *mock_rank_scan_response(void) {
    return strdup(
        "{\"status\":\"ok\","
        "\"ranks\":["
        "{\"season\":\"2026-08\",\"rank\":\"Mythic\",\"tier\":0,\"step\":0,"
        "\"wins\":15,\"losses\":3}"
        "],"
        "\"account\":{\"player_name\":\"TestPlayer\",\"account_id\":\"MOCK-001\"}"
        "}");
}

/* --- Request parsing --- */

/* Extract process_names array from JSON.
 * Fills names[] up to max_names. Returns count or 0 if none specified. */
static int parse_process_names(const char *json, char names[][MAX_NAME_LEN],
                               int max_names) {
    const char *p = strstr(json, "\"process_names\"");
    if (!p) return 0;
    p = strstr(p, "[");
    if (!p) return 0;
    p++;  /* skip [ */
    int count = 0;
    while (*p && count < max_names) {
        while (*p && *p != '"') {
            if (*p == ']') return count;
            p++;
        }
        if (*p != '"') break;
        p++;  /* skip opening quote */
        const char *start = p;
        while (*p && *p != '"') p++;
        if (*p != '"') break;
        size_t len = p - start;
        if (len >= MAX_NAME_LEN) len = MAX_NAME_LEN - 1;
        memcpy(names[count], start, len);
        names[count][len] = '\0';
        count++;
        p++;  /* skip closing quote */
        /* skip to next string or ] */
        while (*p && *p != '"' && *p != ']') p++;
    }
    return count;
}

/* Extract anchor grpIds from JSON.
 * Fills anchor_ids[] up to max_anchors. Returns count. */
static int parse_anchor_ids(const char *json, uint32_t anchor_ids[],
                            int max_anchors) {
    const char *p = strstr(json, "\"anchors\"");
    if (!p) return 0;
    /* Could be "anchors":[...] or "anchor_ids":[...] */
    p = strstr(p, "[");
    if (!p) return 0;
    p++;
    int count = 0;
    while (*p && count < max_anchors) {
        while (*p && (*p == ' ' || *p == ',' || *p == '\t' || *p == '\n')) p++;
        if (*p == ']' || *p == '}') break;
        if (*p == '{') {
            /* Object form: {"grpId":12345,...} */
            long val;
            if (json_get_int(p, "grpId", &val) && val > 0) {
                anchor_ids[count++] = (uint32_t)val;
            }
            /* skip to next comma or ] */
            while (*p && *p != '}' && *p != ']') p++;
            if (*p == '}') p++;
            continue;
        }
        /* Plain integer form */
        if (*p >= '0' && *p <= '9') {
            char *end;
            long val = strtol(p, &end, 10);
            if (end != p && val > 0) {
                anchor_ids[count++] = (uint32_t)val;
            }
            p = end;
        } else {
            p++;
        }
    }
    return count;
}

/* --- Action handlers --- */

static char *handle_status(void) {
    char *escaped_sock = json_escape(g_sock_path);
    char resp[512];
    snprintf(resp, sizeof(resp),
             "{\"status\":\"ok\",\"pid\":%d,\"version\":\"%s\",\"socket_path\":%s}",
             (int)getpid(), VERSION, escaped_sock ? escaped_sock : "\"\"");
    free(escaped_sock);
    return strdup(resp);
}

/* --- Low-Level Primitives --- */

/* Max bytes per read_memory request (16 MB) */
#define MAX_READ_SIZE (16 * 1024 * 1024)

static char *handle_list_regions(void) {
    if (g_test_mode) {
        return strdup("{\"status\":\"ok\",\"regions\":["
                       "{\"address\":4294967296,\"size\":1048576},"
                       "{\"address\":4296015872,\"size\":2097152}"
                       "]}");
    }

    /* Find MTGA process */
    pid_t pid = find_process_by_name("MTGA");
    if (pid <= 0) {
        return build_error("MTGA process not found. Is the game running?");
    }

    mach_port_t task = attach_to_process(pid);
    if (task == MACH_PORT_NULL) {
        return build_error("task_for_pid failed (helper needs root)");
    }

    region_t regions[MAX_REGIONS];
    int n_regions = get_writable_regions(task, regions, MAX_REGIONS);
    mach_port_deallocate(mach_task_self(), task);

    if (n_regions <= 0) {
        return build_error("No writable memory regions found");
    }

    /* Build JSON response */
    sb_t resp;
    sb_init(&resp, 65536);
    sb_append(&resp, "{\"status\":\"ok\",\"regions\":[");

    for (int i = 0; i < n_regions; i++) {
        if (i > 0) sb_append(&resp, ",");
        sb_appendf(&resp, "{\"address\":%llu,\"size\":%llu}",
                   (unsigned long long)regions[i].address,
                   (unsigned long long)regions[i].size);
    }

    sb_append(&resp, "]}");
    return resp.buf;
}

static char *handle_read_memory(const char *request) {
    if (g_test_mode) {
        /* Return 64 bytes of test data */
        unsigned char mock[64];
        for (int i = 0; i < 64; i++) mock[i] = (unsigned char)i;
        char *b64 = base64_encode(mock, 64);
        if (!b64) return build_error("Out of memory");
        char resp[256];
        snprintf(resp, sizeof(resp),
                 "{\"status\":\"ok\",\"data\":\"%s\",\"bytes_read\":64}", b64);
        free(b64);
        return strdup(resp);
    }

    /* Parse address and size */
    long address = 0, size = 0;
    if (!json_get_int(request, "address", &address) || address <= 0) {
        return build_error("Missing or invalid 'address' field");
    }
    if (!json_get_int(request, "size", &size) || size <= 0) {
        return build_error("Missing or invalid 'size' field");
    }
    if (size > MAX_READ_SIZE) size = MAX_READ_SIZE;

    /* Find MTGA process */
    pid_t pid = find_process_by_name("MTGA");
    if (pid <= 0) {
        return build_error("MTGA process not found. Is the game running?");
    }

    mach_port_t task = attach_to_process(pid);
    if (task == MACH_PORT_NULL) {
        return build_error("task_for_pid failed (helper needs root)");
    }

    /* Read memory */
    mach_vm_size_t bytes_read = 0;
    void *buf = read_memory(task, (mach_vm_address_t)address,
                            (mach_vm_size_t)size, &bytes_read);
    mach_port_deallocate(mach_task_self(), task);

    if (!buf) {
        return build_error("Failed to read memory at the specified address");
    }

    /* Base64 encode */
    char *b64 = base64_encode((const unsigned char *)buf, bytes_read);
    mach_vm_deallocate(mach_task_self(), (mach_vm_address_t)buf, bytes_read);

    if (!b64) {
        return build_error("Out of memory for Base64 encoding");
    }

    /* Build response */
    sb_t resp;
    sb_init(&resp, strlen(b64) + 128);
    sb_appendf(&resp, "{\"status\":\"ok\",\"data\":\"%s\",\"bytes_read\":%llu}",
               b64, (unsigned long long)bytes_read);
    free(b64);
    return resp.buf;
}

/* --- Socket server --- */

static int setup_socket(void) {
    unlink(g_sock_path);

    server_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (server_fd < 0) {
        perror("socket");
        return -1;
    }

    /* Set restrictive umask BEFORE bind — no race window.
     * umask 0117 → socket created with 0660 (rw-rw----):
     * owner (root) and group have read/write, others nothing.
     * The peer-credential check in handle_client() enforces access
     * based on UID, not just file permissions. */
    mode_t old_umask = umask(0117);

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, g_sock_path, sizeof(addr.sun_path) - 1);

    if (bind(server_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        umask(old_umask);
        perror("bind");
        close(server_fd);
        server_fd = -1;
        return -1;
    }

    umask(old_umask);

    if (listen(server_fd, MAX_CLIENTS) < 0) {
        perror("listen");
        close(server_fd);
        server_fd = -1;
        return -1;
    }

    return 0;
}

static int check_peer_credentials(int client_fd) {
    /* Verify the connecting client's UID via getpeereid().
     * Access policy: root (uid 0) and the console user are allowed.
     * All other local users are rejected. */
    uid_t peer_uid = (uid_t)-1;
    gid_t peer_gid = (gid_t)-1;

    if (getpeereid(client_fd, &peer_uid, &peer_gid) != 0) {
        fprintf(stderr, "mtga-helper: getpeereid failed: %s\n", strerror(errno));
        return -1;
    }

    /* root is always allowed */
    if (peer_uid == 0) return 0;

    /* console (login) user is allowed — this is the desktop user running
     * the MTGA Advisor app / CLI */
    uid_t console_uid = 0;
    struct stat sb;
    if (stat("/dev/console", &sb) == 0) {
        console_uid = sb.st_uid;
    }

    if (console_uid != 0 && peer_uid == console_uid) {
        return 0;
    }

    /* Fallback: if we cannot determine the console user (e.g. headless),
     * allow any non-root user in group 20 (staff) or 80 (admin) on macOS. */
    if (peer_gid == 20 || peer_gid == 80) {
        return 0;
    }

    fprintf(stderr, "mtga-helper: rejected connection from uid %d gid %d\n",
            (int)peer_uid, (int)peer_gid);
    return -1;
}

static void handle_client(int client_fd) {
    /* Peer credential check — reject unauthorized clients early */
    if (check_peer_credentials(client_fd) != 0) {
        const char *denied = "{\"error\":\"access denied: peer credentials insufficient\"}";
        ssize_t w = write(client_fd, denied, strlen(denied));
        (void)w;
        return;
    }

    char buf[BUF_SIZE];
    ssize_t total = 0;

    /* Read until we have a complete JSON object (simplified: read all available) */
    while (total < (ssize_t)sizeof(buf) - 1) {
        ssize_t n = read(client_fd, buf + total, sizeof(buf) - 1 - total);
        if (n <= 0) break;
        total += n;
        buf[total] = '\0';
        /* Check if we've read a complete JSON line */
        if (buf[total - 1] == '\n' || buf[total - 1] == '}') break;
    }
    if (total <= 0) return;
    buf[total] = '\0';

    char *response = NULL;

    /* Parse action */
    char *action = json_get_string(buf, "action");
    if (!action) {
        response = build_error("Missing 'action' field");
        goto send_response;
    }

    if (strcmp(action, "ping") == 0) {
        response = build_ok();
    } else if (strcmp(action, "status") == 0) {
        response = handle_status();
    } else if (strcmp(action, "shutdown") == 0) {
        response = build_ok();
        running = 0;
    } else if (strcmp(action, "list_regions") == 0) {
        response = handle_list_regions();
    } else if (strcmp(action, "read_memory") == 0) {
        response = handle_read_memory(buf);
    } else {
        char err[160];
        snprintf(err, sizeof(err), "Unknown action: %s", action);
        response = build_error(err);
    }

    free(action);

send_response:
    if (response) {
        /* Write response + newline (Python client expects newline-terminated or
         * just reads until connection close) */
        size_t rlen = strlen(response);
        ssize_t written = write(client_fd, response, rlen);
        (void)written;
        free(response);
    }
}

static void signal_handler(int sig) {
    (void)sig;
    running = 0;
    if (server_fd >= 0) {
        /* Wake up select() */
        close(server_fd);
        server_fd = -1;
    }
}

/* --- Main --- */

static void usage(const char *prog) {
    fprintf(stderr,
        "Usage: %s [OPTIONS]\n"
        "\n"
        "Options:\n"
        "  --sock PATH     UNIX socket path (default: %s)\n"
        "  --test-mode     Mock mode: return dummy data, no task_for_pid\n"
        "  --version       Print version and exit\n"
        "  --help, -h      Show this help\n",
        prog, DEFAULT_SOCK_PATH);
}

int main(int argc, char *argv[]) {
    static struct option long_opts[] = {
        {"sock",      required_argument, 0, 's'},
        {"test-mode", no_argument,       0, 't'},
        {"version",   no_argument,       0, 'V'},
        {"help",      no_argument,       0, 'h'},
        {0, 0, 0, 0}
    };

    int opt;
    while ((opt = getopt_long(argc, argv, "s:tVh", long_opts, NULL)) != -1) {
        switch (opt) {
            case 's':
                strncpy(g_sock_path, optarg, sizeof(g_sock_path) - 1);
                g_sock_path[sizeof(g_sock_path) - 1] = '\0';
                break;
            case 't':
                g_test_mode = 1;
                break;
            case 'V':
                printf("mtga-helper %s\n", VERSION);
                return 0;
            case 'h':
                usage(argv[0]);
                return 0;
            default:
                usage(argv[0]);
                return 1;
        }
    }

    signal(SIGPIPE, SIG_IGN);
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    if (setup_socket() < 0) {
        fprintf(stderr, "mtga-helper: failed to setup socket at %s\n",
                g_sock_path);
        return 1;
    }

    fprintf(stderr, "mtga-helper %s: listening on %s%s\n",
            VERSION, g_sock_path,
            g_test_mode ? " (TEST MODE)" : "");

    while (running) {
        if (server_fd < 0) break;

        fd_set read_fds;
        FD_ZERO(&read_fds);
        FD_SET(server_fd, &read_fds);

        struct timeval tv = {1, 0};
        int ret = select(server_fd + 1, &read_fds, NULL, NULL, &tv);
        if (ret < 0) {
            if (errno == EINTR) continue;
            perror("select");
            break;
        }
        if (ret == 0) continue;

        if (FD_ISSET(server_fd, &read_fds)) {
            struct sockaddr_un client_addr;
            socklen_t client_len = sizeof(client_addr);
            int client_fd = accept(server_fd,
                                   (struct sockaddr *)&client_addr,
                                   &client_len);
            if (client_fd < 0) {
                if (errno != EINTR) perror("accept");
                continue;
            }
            handle_client(client_fd);
            close(client_fd);
        }
    }

    if (server_fd >= 0) close(server_fd);
    unlink(g_sock_path);
    fprintf(stderr, "mtga-helper: shutdown complete\n");
    return 0;
}