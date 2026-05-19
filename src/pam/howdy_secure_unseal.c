/*
 * howdy-secure-unseal
 *
 * Minimal setuid helper that runs tpm2_unseal against the sealed blob and
 * writes the plaintext secret to stdout. The PAM module (running as root)
 * calls this to avoid needing tpm2-tools in the PAM module itself.
 *
 * Usage:  howdy-secure-unseal <blob_path>
 *
 * Exits 0 on success, non-zero on failure.
 * Secret is written raw to stdout (no newline appended).
 *
 * Build:
 *   gcc -o howdy-secure-unseal howdy_secure_unseal.c -Wall -Wextra
 *   sudo chown root:root howdy-secure-unseal
 *   sudo chmod 4755 howdy-secure-unseal   # setuid root
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <errno.h>
#include <fcntl.h>

#define MAX_BLOB_SIZE  (64 * 1024)
#define MAX_SECRET     512

/* Simple struct layout matching tpm_utils.py: [4-byte BE pub_len][pub][priv] */

static uint32_t read_be32(const unsigned char *p)
{
    return ((uint32_t)p[0] << 24) |
           ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] <<  8) |
           ((uint32_t)p[3]);
}

static int write_tmpfile(const char *dir, const char *name,
                         const unsigned char *data, size_t len,
                         char *out_path, size_t out_path_sz)
{
    snprintf(out_path, out_path_sz, "%s/%s", dir, name);
    int fd = open(out_path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (fd < 0) return -1;
    if (write(fd, data, len) != (ssize_t)len) { close(fd); return -1; }
    close(fd);
    return 0;
}

static int run_cmd(char *const argv[], char *secret_buf, int *secret_len)
{
    int pipefd[2];
    if (pipe(pipefd) != 0) return -1;

    pid_t pid = fork();
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); return -1; }

    if (pid == 0) {
        close(pipefd[0]);
        dup2(pipefd[1], STDOUT_FILENO);
        close(pipefd[1]);
        int devnull = open("/dev/null", O_WRONLY);
        if (devnull >= 0) { dup2(devnull, STDERR_FILENO); close(devnull); }
        execvp(argv[0], argv);
        _exit(127);
    }

    close(pipefd[1]);
    ssize_t n, total = 0;
    while (total < MAX_SECRET &&
           (n = read(pipefd[0], secret_buf + total, MAX_SECRET - total)) > 0)
        total += n;
    /* drain remaining output so the child doesn't die on SIGPIPE */
    char drain[4096];
    while (read(pipefd[0], drain, sizeof(drain)) > 0) {}
    close(pipefd[0]);

    int status;
    waitpid(pid, &status, 0);

    if (!WIFEXITED(status) || WEXITSTATUS(status) != 0)
        return -1;

    *secret_len = (int)total;
    return 0;
}

int main(int argc, char *argv[])
{
    if (argc != 2) {
        fprintf(stderr, "usage: howdy-secure-unseal <blob_path>\n");
        return 1;
    }

    const char *blob_path = argv[1];

    /* Security: reject blob paths outside /etc/howdy-secure */
    if (strncmp(blob_path, "/etc/howdy-secure/", 18) != 0) {
        fprintf(stderr, "error: blob path must be under /etc/howdy-secure/\n");
        return 1;
    }

    /* Read blob */
    FILE *f = fopen(blob_path, "rb");
    if (!f) { perror("fopen blob"); return 1; }
    unsigned char *blob = malloc(MAX_BLOB_SIZE);
    if (!blob) { fclose(f); return 1; }
    size_t blob_len = fread(blob, 1, MAX_BLOB_SIZE, f);
    fclose(f);

    if (blob_len < 4) { fprintf(stderr, "blob too small\n"); free(blob); return 1; }

    uint32_t pub_len  = read_be32(blob);
    size_t   priv_off = 4 + pub_len;
    if (priv_off >= blob_len) {
        fprintf(stderr, "blob corrupt\n"); free(blob); return 1;
    }

    const unsigned char *pub_data  = blob + 4;
    const unsigned char *priv_data = blob + priv_off;
    size_t               priv_len  = blob_len - priv_off;

    /* Write to tmpfs */
    char tmpdir[] = "/run/howdy-secure-unseal-XXXXXX";
    if (!mkdtemp(tmpdir)) { perror("mkdtemp"); free(blob); return 1; }

    char pub_path[256], priv_path[256], prim_ctx[256], obj_ctx[256];
    write_tmpfile(tmpdir, "sealed.pub",  pub_data,  pub_len,  pub_path,  sizeof(pub_path));
    write_tmpfile(tmpdir, "sealed.priv", priv_data, priv_len, priv_path, sizeof(priv_path));
    snprintf(prim_ctx, sizeof(prim_ctx), "%s/primary.ctx", tmpdir);
    snprintf(obj_ctx,  sizeof(obj_ctx),  "%s/sealed.ctx",  tmpdir);
    free(blob);

    /* tpm2_createprimary */
    char *create_argv[] = {
        "tpm2_createprimary",
        "--hierarchy", "o",
        "--key-algorithm", "rsa2048:null:aes128cfb",
        "--key-context", prim_ctx,
        NULL
    };
    char dummy[MAX_SECRET]; int dummy_len = 0;
    if (run_cmd(create_argv, dummy, &dummy_len) != 0) {
        fprintf(stderr, "tpm2_createprimary failed\n");
        return 1;
    }

    /* tpm2_load */
    char *load_argv[] = {
        "tpm2_load",
        "--parent-context", prim_ctx,
        "--public", pub_path,
        "--private", priv_path,
        "--key-context", obj_ctx,
        NULL
    };
    if (run_cmd(load_argv, dummy, &dummy_len) != 0) {
        fprintf(stderr, "tpm2_load failed\n");
        return 1;
    }

    /* tpm2_unseal — capture secret */
    char *unseal_argv[] = {
        "tpm2_unseal",
        "--object-context", obj_ctx,
        NULL
    };
    char secret[MAX_SECRET];
    int  secret_len = 0;
    if (run_cmd(unseal_argv, secret, &secret_len) != 0) {
        fprintf(stderr, "tpm2_unseal failed\n");
        explicit_bzero(secret, sizeof(secret));
        return 1;
    }

    /* Write secret to stdout, then zero it */
    fwrite(secret, 1, secret_len, stdout);
    fflush(stdout);
    explicit_bzero(secret, sizeof(secret));
    return 0;
}
