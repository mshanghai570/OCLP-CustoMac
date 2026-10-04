/* Retired: personal builds use macOS administrator authorization.
 * Never re-enable a setuid broker for unsigned application callers. */
#include <stdio.h>
int main(int argc, const char *argv[]) {
    fputs("This privileged helper is retired. Reinstall OCLP-CustoMac.\n", stderr);
    return 170;
}
