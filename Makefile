CC      = gcc
CFLAGS  = -Wall -Wextra -fPIC -O2
LDFLAGS = -lpam

PAM_SO     = pam_howdy_secure.so
UNSEAL_BIN = howdy-secure-unseal
PAM_DIR    = /usr/lib/security
BIN_DIR    = /usr/local/bin
CLI_DIR    = /usr/local/lib/howdy-secure

.PHONY: all clean install uninstall

all: $(PAM_SO) $(UNSEAL_BIN)

$(PAM_SO): src/pam/pam_howdy_secure.c
	$(CC) $(CFLAGS) -shared -o $@ $< $(LDFLAGS)

$(UNSEAL_BIN): src/pam/howdy_secure_unseal.c
	$(CC) $(CFLAGS) -o $@ $<

install: all
	@echo "Installing PAM module..."
	install -m 644 $(PAM_SO) $(PAM_DIR)/$(PAM_SO)
	@echo "Installing unseal helper (setuid root)..."
	install -m 4755 -o root $(UNSEAL_BIN) $(BIN_DIR)/$(UNSEAL_BIN)
	@echo "Installing CLI..."
	install -d $(CLI_DIR)
	install -m 644 src/cli/*.py $(CLI_DIR)/
	install -m 755 src/cli/howdy-secure $(BIN_DIR)/howdy-secure
	@echo "Done. Run: sudo howdy-secure setup"

uninstall:
	rm -f $(PAM_DIR)/$(PAM_SO)
	rm -f $(BIN_DIR)/$(UNSEAL_BIN)
	rm -f $(BIN_DIR)/howdy-secure
	rm -rf $(CLI_DIR)
	@echo "Uninstalled. Run 'sudo howdy-secure remove' first to clean PAM config and TPM."

clean:
	rm -f $(PAM_SO) $(UNSEAL_BIN)
