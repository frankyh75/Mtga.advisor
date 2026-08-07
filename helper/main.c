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
#define READ_CHUNK (4 * 1024 * 1024)
#define MAX_REGIONS 4096

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

static void sb_free(sb_t *sb) __attribute__((unused));
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
static pid_t find_process(const char *process_names[], int n_names) __attribute__((unused));
static pid_t find_process(const char *process_names[], int n_names) {
    for (int i = 0; i < n_names; i++) {
        pid_t pid = find_process_by_name(process_names[i]);
        if (pid > 0) return pid;
    }
    /* Default: try "MTGA" */
    return find_process_by_name("MTGA");
}

/* --- Memory reading utilities --- */

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
 * Uses mach_vm_read_overwrite (into a caller-provided malloc'd buffer) instead
 * of mach_vm_read (which vm_allocates and returns a vm_offset_t handle).
 *
 * Why: mach_vm_read fails / returns zeros for read-only or mixed-permission
 * regions (e.g. the global-metadata.dat mapping, GameAssembly.dylib __DATA),
 * which is exactly where the IL2CPP FieldInfo arrays and class structs live.
 * mach_vm_read_overwrite copies into our own buffer regardless of the source
 * region's protection, so the backref discovery can find them.
 *
 * Returns malloc'd buffer (caller frees via free()) or NULL. */
static void *read_memory(mach_port_t task, mach_vm_address_t addr,
                         mach_vm_size_t size, mach_vm_size_t *out_size) {
    void *buf = malloc(size);
    if (!buf) return NULL;

    mach_vm_size_t data_count = size;
    kern_return_t kr = mach_vm_read_overwrite(task, addr, size,
                                              (mach_vm_address_t)buf, &data_count);
    if (kr != KERN_SUCCESS) {
        free(buf);
        return NULL;
    }
    *out_size = data_count;
    return buf;
}

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

    /* Guard gegen Endlosschleifen bei leeren Submaps: merke uns die
     * letzte Submap (address+size+depth). Wenn mach_vm_region_recurse
     * nach depth++ dieselbe Submap nochmal liefert, ist sie leer —
     * dann überspringen wir sie (address += size; depth--). */
    mach_vm_address_t last_submap_addr = 0;
    mach_vm_size_t last_submap_size = 0;
    natural_t last_submap_depth = 0;
    int submap_retry = 0;

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
            /* Guard: wenn wir gerade depth++ für diese exakte Submap gemacht
             * haben und sie wiederkehrt, ist sie leer → überspringen. */
            if (submap_retry && address == last_submap_addr && size == last_submap_size) {
                address += size;
                depth = last_submap_depth;  /* zurück auf die Ebene vor der Submap */
                submap_retry = 0;
                continue;
            }
            /* In die Submap eintauchen: nur depth++ (nicht address += size!).
             * mach_vm_region_recurse verwaltet depth als in/out-Parameter:
             *   - depth++ → nächste Iteration liefert Regionen INNERHALB der
             *     Submap (address wird auf die erste innere Region gesetzt)
             *   - Wenn alle inneren Regionen enumeriert sind, fährt die
             *     Enumeration automatisch nach der Submap fort.
             *
             * Der alte Bug: depth++ UND address += size übersprang die
             * gesamte Submap → nur 12 statt ~80+ Regionen. */
            last_submap_addr = address;
            last_submap_size = size;
            last_submap_depth = depth;
            depth++;
            submap_retry = 1;
            continue;
        }

        submap_retry = 0;  /* non-submap region → reset guard */

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

/* Forward declarations — these functions are defined later in the file
 * but referenced by handle_list_regions / handle_read_memory. */
static mach_port_t attach_to_process(pid_t pid);
static int get_writable_regions(mach_port_t task, region_t *regions, int max_regions);
static void *read_memory(mach_port_t task, mach_vm_address_t addr,
                         mach_vm_size_t size, mach_vm_size_t *out_size);

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
    free(buf);  /* buf ist jetzt malloc'd (mach_vm_read_overwrite), nicht vm_allocated */

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

/* --- Mock scan handler (test-mode only) --- */

/* --- Socket server --- */

static int setup_socket(void) {
    unlink(g_sock_path);

    server_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (server_fd < 0) {
        perror("socket");
        return -1;
    }

    /* Set permissive umask BEFORE bind — no race window.
     * umask 0111 → socket created with 0666 (rw-rw-rw-):
     * any local user can connect(). Access control is enforced
     * entirely by check_peer_credentials() in handle_client(),
     * which verifies the peer UID via getpeereid() and allows
     * only root (uid 0) and the console user.
     *
     * Rationale: the daemon runs as root. If we restricted the
     * socket file to 0660 root:daemon, a normal console user
     * (e.g. frankhermann, who is not in group daemon) could not
     * even call connect() — the kernel rejects the connection
     * before check_peer_credentials() ever runs. Making the socket
     * world-connectable and relying on the in-code UID check is
     * the standard approach for root daemons with application-level
     * access control. */
    mode_t old_umask = umask(0111);

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

    /* Belt-and-suspenders: explicitly chmod the socket to 0666.
     * umask should already produce 0666, but an inherited umask
     * from launchd could interfere. This guarantees the mode
     * regardless of the process umask at start time. */
    if (chmod(g_sock_path, 0666) != 0) {
        perror("chmod socket");
        /* non-fatal — the umask path should have worked */
    }

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

    /* No fallback — reject. Only root and the console user are allowed. */
    fprintf(stderr, "mtga-helper: rejected connection from uid %d gid %d\n",
            (int)peer_uid, (int)peer_gid);
    return -1;
}

/* Write the entire buffer to a socket, looping until all bytes are sent.
 * A single write() on a UNIX socket may send only part of a large buffer
 * (e.g. a multi-MB base64 read_memory response) and return the partial
 * count; the rest must be written in subsequent calls. Returns 0 on
 * success, -1 on error. */
static int write_all(int fd, const char *buf, size_t len) {
    size_t off = 0;
    while (off < len) {
        ssize_t n = write(fd, buf + off, len - off);
        if (n < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        if (n == 0) return -1;
        off += (size_t)n;
    }
    return 0;
}

/* Send a response to the client, appending a trailing newline so the client
 * can reliably detect the end of the JSON message (the protocol is
 * newline-delimited, and without the \n the client would keep reading past a
 * large multi-MB response until a timeout). Also frees `response`. */
static void send_response(int client_fd, char *response) {
    if (!response) return;
    write_all(client_fd, response, strlen(response));
    write_all(client_fd, "\n", 1);
    free(response);
}

/* Process a single JSON request and send the response back on client_fd.
 * Returns 0 on success, -1 on read error / connection closed, 1 if the
 * client requested "shutdown" (so the caller can stop the server loop). */
static int process_request(int client_fd, const char *buf) {
    char *response = NULL;

    char *action = json_get_string(buf, "action");
    if (!action) {
        response = build_error("Missing 'action' field");
    } else if (strcmp(action, "ping") == 0) {
        response = build_ok();
    } else if (strcmp(action, "status") == 0) {
        response = handle_status();
    } else if (strcmp(action, "shutdown") == 0) {
        response = build_ok();
        free(action);
        send_response(client_fd, response);
        return 1;  /* signal shutdown */
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

    send_response(client_fd, response);
    return 0;
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

    /* Keep-alive loop: the client may send multiple JSON requests on the
     * same connection (e.g. a combined collection+deck+rank scan uses one
     * socket for all three). We read newline-terminated JSON lines and
     * dispatch them one by one until the client closes or sends shutdown. */
    while (running) {
        ssize_t total = 0;

        while (total < (ssize_t)sizeof(buf) - 1) {
            ssize_t n = read(client_fd, buf + total, sizeof(buf) - 1 - total);
            if (n <= 0) return;  /* client closed or error */
            total += n;
            buf[total] = '\0';
            if (buf[total - 1] == '\n') break;
        }
        if (total <= 0) return;
        /* Strip trailing newline for the parser */
        while (total > 0 && (buf[total - 1] == '\n' || buf[total - 1] == '\r')) {
            buf[--total] = '\0';
        }
        if (total == 0) continue;

        int rc = process_request(client_fd, buf);
        if (rc == 1) {  /* shutdown requested */
            running = 0;
            return;
        }
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