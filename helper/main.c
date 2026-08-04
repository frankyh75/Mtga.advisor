/**
 * mtga-helper — SMJobBless-kompatibler Memory-Scanner
 *
 * Horcht auf UNIX Socket /tmp/mtga-helper.sock, akzeptiert JSON-Kommandos:
 *   {"action":"ping"}              → {"status":"ok"}
 *   {"action":"scan","pid":12345}  → {"status":"ok","collection":{...},"decks":[...]}
 *   {"action":"shutdown"}          → Server beendet sich
 *
 * Kompilieren: clang -o mtga-helper main.c -framework CoreFoundation
 * Starten (als root): ./mtga-helper
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/select.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach/vm_region.h>
#include <sys/stat.h>
#include <CoreFoundation/CoreFoundation.h>

#define SOCKET_PATH "/tmp/mtga-helper.sock"
#define MAX_CLIENTS 8
#define BUF_SIZE 65536
#define MAX_RESPONSE 1048576  /* 1 MB */

static int server_fd = -1;
static volatile int running = 1;

/* --- JSON Builder (einfach, kein Parser nötig) --- */

static char *json_escape(const char *s) {
    if (!s) return strdup("null");
    size_t len = strlen(s);
    size_t cap = len * 2 + 3;
    char *out = malloc(cap);
    if (!out) return NULL;
    char *p = out;
    *p++ = '"';
    for (size_t i = 0; i < len; i++) {
        unsigned char c = s[i];
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
                    *p++ = c;
                }
                break;
        }
    }
    *p++ = '"';
    *p = '\0';
    return out;
}

static char *build_error(const char *msg) {
    char *escaped = json_escape(msg);
    if (!escaped) return NULL;
    size_t len = strlen(escaped) + 50;
    char *resp = malloc(len);
    if (!resp) { free(escaped); return NULL; }
    snprintf(resp, len, "{\"status\":\"error\",\"message\":%s}", escaped);
    free(escaped);
    return resp;
}

static char *build_pong(void) {
    return strdup("{\"status\":\"ok\"}");
}

/* --- Memory Scanner --- */

static kern_return_t read_process_memory(pid_t pid, mach_vm_address_t addr,
                                          mach_vm_size_t size, void **buf,
                                          mach_vm_size_t *out_size) {
    mach_port_t task;
    kern_return_t kr = task_for_pid(mach_task_self(), pid, &task);
    if (kr != KERN_SUCCESS) return kr;

    pointer_t data;
    mach_msg_type_number_t data_size;
    kr = mach_vm_read(task, addr, size, &data, &data_size);
    if (kr == KERN_SUCCESS) {
        *buf = (void *)data;
        *out_size = data_size;
    }
    mach_port_deallocate(mach_task_self(), task);
    return kr;
}

/* Einfacher Scan: sucht nach Arena-Card-IDs im Speicher eines Prozesses.
   Das ist eine vereinfachte Version — der echte Scan nutzt Pattern-Matching
   wie in scanner/pattern_scanner.py. */
static char *scan_process(pid_t pid) {
    mach_port_t task;
    kern_return_t kr = task_for_pid(mach_task_self(), pid, &task);
    if (kr != KERN_SUCCESS) {
        char err[256];
        snprintf(err, sizeof(err), "task_for_pid failed: %s (run as root?)",
                 mach_error_string(kr));
        return build_error(err);
    }

    /* Regionen durchlaufen — vereinfachte Version */
    mach_vm_address_t address = 0;
    mach_vm_size_t size;
    struct vm_region_basic_info_64 info;
    mach_msg_type_number_t info_count = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t object_name;

    int region_count = 0;
    mach_vm_size_t total_size = 0;

    while (1) {
        kr = mach_vm_region(task, &address, &size, VM_REGION_BASIC_INFO_64,
                            (vm_region_info_t)&info, &info_count, &object_name);
        if (kr != KERN_SUCCESS) break;
        region_count++;
        total_size += size;
        address += size;
        info_count = VM_REGION_BASIC_INFO_COUNT_64;
    }

    mach_port_deallocate(mach_task_self(), task);

    /* JSON-Antwort bauen */
    char resp[MAX_RESPONSE];
    int n = snprintf(resp, sizeof(resp),
        "{\"status\":\"ok\",\"scan\":{"
        "\"pid\":%d,"
        "\"regions\":%d,"
        "\"bytes_total\":%llu,"
        "\"note\":\"Vollständiger Pattern-Scan folgt in T3\""
        "}}",
        pid, region_count, (unsigned long long)total_size);

    if (n >= (int)sizeof(resp)) {
        return build_error("Response too large");
    }

    return strdup(resp);
}

/* --- JSON Parser (minimal) --- */

static const char *json_find_string(const char *json, const char *key) {
    /* Sucht "key":"..." im JSON-String */
    char pattern[256];
    snprintf(pattern, sizeof(pattern), "\"%s\":\"", key);
    const char *p = strstr(json, pattern);
    if (!p) return NULL;
    p += strlen(pattern);
    return p; /* zeigt auf Start des Werts */
}

static const char *json_find_int(const char *json, const char *key, long *val) {
    char pattern[256];
    snprintf(pattern, sizeof(pattern), "\"%s\":", key);
    const char *p = strstr(json, pattern);
    if (!p) return NULL;
    p += strlen(pattern);
    while (*p == ' ') p++;
    if (*p < '0' || *p > '9') return NULL;
    *val = strtol(p, NULL, 10);
    return p;
}

/* --- Socket Server --- */

static int setup_socket(void) {
    unlink(SOCKET_PATH);

    server_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (server_fd < 0) {
        perror("socket");
        return -1;
    }

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, SOCKET_PATH, sizeof(addr.sun_path) - 1);

    if (bind(server_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("bind");
        close(server_fd);
        return -1;
    }

    /* Socket-Berechtigungen: nur root und owner */
    chmod(SOCKET_PATH, 0600);

    if (listen(server_fd, MAX_CLIENTS) < 0) {
        perror("listen");
        close(server_fd);
        return -1;
    }

    return 0;
}

static void handle_client(int client_fd) {
    char buf[BUF_SIZE];
    ssize_t n = read(client_fd, buf, sizeof(buf) - 1);
    if (n <= 0) return;
    buf[n] = '\0';

    char *response = NULL;

    /* Aktion parsen */
    const char *action_start = json_find_string(buf, "action");
    if (!action_start) {
        response = build_error("Missing 'action' field");
        goto send_response;
    }

    /* Extrahiere action-Wert (bis zum nächsten ") */
    char action[64];
    const char *end = strchr(action_start, '"');
    if (!end || (size_t)(end - action_start) >= sizeof(action)) {
        response = build_error("Invalid 'action' value");
        goto send_response;
    }
    size_t alen = end - action_start;
    memcpy(action, action_start, alen);
    action[alen] = '\0';

    if (strcmp(action, "ping") == 0) {
        response = build_pong();
    } else if (strcmp(action, "shutdown") == 0) {
        response = build_pong();
        running = 0;
    } else if (strcmp(action, "scan") == 0) {
        long pid = 0;
        if (!json_find_int(buf, "pid", &pid) || pid <= 0) {
            response = build_error("Missing or invalid 'pid' field");
        } else {
            response = scan_process((pid_t)pid);
        }
    } else {
        char err[128];
        snprintf(err, sizeof(err), "Unknown action: %s", action);
        response = build_error(err);
    }

send_response:
    if (response) {
        size_t rlen = strlen(response);
        write(client_fd, response, rlen);
        free(response);
    }
}

static void signal_handler(int sig) {
    (void)sig;
    running = 0;
}

int main(int argc __attribute__((unused)), char **argv __attribute__((unused))) {
    /* Ignoriere SIGPIPE */
    signal(SIGPIPE, SIG_IGN);
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    /* Root-Check: für Testzwecke deaktiviert
    if (geteuid() != 0) {
        fprintf(stderr, "mtga-helper: must be run as root (task_for_pid needs it)\n");
        return 1;
    }
    */

    if (setup_socket() < 0) {
        fprintf(stderr, "mtga-helper: failed to setup socket\n");
        return 1;
    }

    fprintf(stderr, "mtga-helper: listening on %s\n", SOCKET_PATH);

    fd_set read_fds;
    int max_fd = server_fd;

    while (running) {
        FD_ZERO(&read_fds);
        FD_SET(server_fd, &read_fds);

        struct timeval tv = {1, 0}; /* 1s timeout für running-Check */
        int ret = select(max_fd + 1, &read_fds, NULL, NULL, &tv);
        if (ret < 0) {
            if (errno == EINTR) continue;
            perror("select");
            break;
        }
        if (ret == 0) continue; /* timeout */

        if (FD_ISSET(server_fd, &read_fds)) {
            struct sockaddr_un client_addr;
            socklen_t client_len = sizeof(client_addr);
            int client_fd = accept(server_fd,
                                   (struct sockaddr *)&client_addr,
                                   &client_len);
            if (client_fd < 0) {
                perror("accept");
                continue;
            }
            handle_client(client_fd);
            close(client_fd);
        }
    }

    close(server_fd);
    unlink(SOCKET_PATH);
    fprintf(stderr, "mtga-helper: shutdown complete\n");
    return 0;
}
