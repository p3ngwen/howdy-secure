/*
 * pam_howdy_secure.so
 *
 * PAM session module that unseals the GNOME Keyring password from the TPM
 * and passes it to pam_gnome_keyring so the keyring unlocks automatically
 * after a successful Howdy face-auth login.
 *
 * Build:
 *   gcc -shared -fPIC -o pam_howdy_secure.so pam_howdy_secure.c \
 *       -lpam -Wall -Wextra
 *
 * PAM config (add after the howdy auth line in /etc/pam.d/gdm-password):
 *   auth    optional    pam_howdy_secure.so
 */

#define PAM_SM_AUTH
#define PAM_SM_SESSION

#include <security/pam_modules.h>
#include <security/pam_ext.h>
#include <fcntl.h>
#include <syslog.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#include <sys/stat.h>
#include <errno.h>

#define SEALED_BLOB   "/etc/howdy-secure/sealed.blob"
#define UNSEAL_CMD    "/usr/local/bin/howdy-secure-unseal"
#define MAX_SECRET    512

/* ── helpers ──────────────────────────────────────────────────────────────── */

static int file_exists(const char *path)
{
    struct stat st;
    return stat(path, &st) == 0;
}

/*
 * Run UNSEAL_CMD and capture its stdout (the secret).
 * Returns secret length on success, -1 on failure.
 * Caller must free *out.
 */
static int run_unseal(char **out)
{
    int pipefds[2];
    if (pipe(pipefds) != 0)
        return -1;

    pid_t pid = fork();
    if (pid < 0) {
        close(pipefds[0]);
        close(pipefds[1]);
        return -1;
    }

    if (pid == 0) {
        /* child */
        close(pipefds[0]);
        dup2(pipefds[1], STDOUT_FILENO);
        close(pipefds[1]);

        /* Redirect stderr to /dev/null so TPM noise doesn't leak to tty */
        int devnull = open("/dev/null", O_WRONLY);
        if (devnull >= 0) {
            dup2(devnull, STDERR_FILENO);
            close(devnull);
        }

        execl(UNSEAL_CMD, UNSEAL_CMD, SEALED_BLOB, NULL);
        _exit(127);  /* exec failed */
    }

    /* parent */
    close(pipefds[1]);

    char *buf = calloc(MAX_SECRET + 1, 1);
    if (!buf) {
        close(pipefds[0]);
        waitpid(pid, NULL, 0);
        return -1;
    }

    ssize_t total = 0, n;
    while (total < MAX_SECRET &&
           (n = read(pipefds[0], buf + total, MAX_SECRET - total)) > 0)
        total += n;

    close(pipefds[0]);

    int status;
    waitpid(pid, &status, 0);

    if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
        explicit_bzero(buf, MAX_SECRET + 1);
        free(buf);
        return -1;
    }

    *out = buf;
    return (int)total;
}

/*
 * Push the unsealed password into PAM's authtok stack so that
 * pam_gnome_keyring (which must appear AFTER us in the stack) can pick it up.
 */
static int push_authtok(pam_handle_t *pamh, char *secret, int secret_len)
{
    /* Trim trailing newline if present */
    if (secret_len > 0 && secret[secret_len - 1] == '\n') {
        secret[secret_len - 1] = '\0';
        secret_len--;
    }

    /*
     * pam_set_item copies the value; we zero our buffer immediately after
     * so the plaintext lives in our address space as briefly as possible.
     */
    int ret = pam_set_item(pamh, PAM_AUTHTOK, secret);
    explicit_bzero(secret, secret_len);
    return ret;
}

/* ── PAM entry points ─────────────────────────────────────────────────────── */

PAM_EXTERN int pam_sm_authenticate(pam_handle_t *pamh, int flags,
                                   int argc, const char **argv)
{
    (void)flags; (void)argc; (void)argv;

    if (!file_exists(SEALED_BLOB)) {
        /* Not set up — pass through silently (module is optional) */
        pam_syslog(pamh, LOG_DEBUG,
                   "pam_howdy_secure: sealed blob not found, skipping");
        return PAM_IGNORE;
    }

    if (!file_exists(UNSEAL_CMD)) {
        pam_syslog(pamh, LOG_ERR,
                   "pam_howdy_secure: unseal helper not found at " UNSEAL_CMD);
        return PAM_IGNORE;
    }

    char *secret = NULL;
    int secret_len = run_unseal(&secret);
    if (secret_len <= 0) {
        pam_syslog(pamh, LOG_WARNING,
                   "pam_howdy_secure: TPM unseal failed — keyring will not auto-unlock");
        return PAM_IGNORE;
    }

    int ret = push_authtok(pamh, secret, secret_len);
    free(secret);

    if (ret != PAM_SUCCESS) {
        pam_syslog(pamh, LOG_ERR,
                   "pam_howdy_secure: pam_set_item failed: %s",
                   pam_strerror(pamh, ret));
        return PAM_IGNORE;
    }

    pam_syslog(pamh, LOG_DEBUG,
               "pam_howdy_secure: authtok set from TPM — keyring will unlock");
    return PAM_SUCCESS;
}

PAM_EXTERN int pam_sm_setcred(pam_handle_t *pamh, int flags,
                               int argc, const char **argv)
{
    (void)pamh; (void)flags; (void)argc; (void)argv;
    return PAM_SUCCESS;
}

/* Session hooks — not used but required for a complete module */
PAM_EXTERN int pam_sm_open_session(pam_handle_t *pamh, int flags,
                                   int argc, const char **argv)
{
    (void)pamh; (void)flags; (void)argc; (void)argv;
    return PAM_SUCCESS;
}

PAM_EXTERN int pam_sm_close_session(pam_handle_t *pamh, int flags,
                                    int argc, const char **argv)
{
    (void)pamh; (void)flags; (void)argc; (void)argv;
    return PAM_SUCCESS;
}
