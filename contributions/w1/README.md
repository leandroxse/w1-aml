# W1: reproducible source-fix review / revisão reproduzível

## English

This package reviews two changes to the CoreELEC/Amlogic W1 driver without
accessing a TV box. It needs Git, Python 3 and a host C compiler (`cc`). No root
access, firmware download or Python package installation is required.

### What changes and why

1. **Allocation size:** `wifi_mac_free_sta()` requests the structure size rather than its pointer size. In the checked Linux backend, `NET_MALLOC` rounds both requests to 128 bytes. This is a semantic correction, **not a demonstrated heap overflow** on this revision.
2. **Early TX drops:** the original `wifi_mac_hardstart()` can reach its drop handler before classification initializes the TID in `skb->cb`. A stale byte can be 0..255, but the real statistics array has eight elements. Initialize TID before early exits and check the index bound. Early unclassified drops count under TID 0.

The base is exactly `fc5417f19706f85bccb125c1f951c787cf9362f5` from
[CoreELEC/w1-aml](https://github.com/CoreELEC/w1-aml). The name
`v1.8.3.3_20260310-w1` identifies a **branch**, not a tag. Neither patch changes
kernel APIs, board settings, clocks or WDS behavior.

### Run the check

From a checkout containing this package, save its absolute path, then clone a
separate clean upstream tree. Untracked files in the supplied upstream tree
are ignored; modified tracked files or a different HEAD are rejected.

```bash
W1_PACKAGE="$(pwd)/contributions/w1"
W1_TEST="$(mktemp -d /tmp/w1-review.XXXXXX)"
git clone --no-checkout https://github.com/CoreELEC/w1-aml.git "$W1_TEST/upstream"
git -C "$W1_TEST/upstream" checkout --detach fc5417f19706f85bccb125c1f951c787cf9362f5
python3 "$W1_PACKAGE/verify.py" "$W1_TEST/upstream"
```

Expected output includes:

```text
before: requested bytes, allocated bytes, early invalid writes, synthetic classified invalid writes: 8 128 248 248
after: requested bytes, allocated bytes, early invalid writes, synthetic classified invalid writes: 32 128 0 0
PASS: exact base, clean tracked tree, both patches, source-derived host models
```

The verifier reads four pinned source files, applies the two patches in a
temporary directory and compiles a host model using the actual allocation and
counter statements. The model backs the counter with 256 slots to avoid C
undefined behavior: slots 8..255 represent writes beyond the **real** array.
It checks all 256 byte values. Invalid classified TIDs are synthetic defensive
inputs; they are not claimed outputs of `wifi_mac_classify()`.

The model assumes an LP64 host (8-byte pointers, 32-byte station-entry
structure), matching the relevant sizes on AArch64. It rejects a different
host ABI. Set `CC` if the host compiler has another command name.

Exit 0 means source/patch/model checks passed. It does **not** mean modules
were built, loaded or tested in hardware. The supplied checkout is not patched.

### Apply manually to the disposable upstream checkout

```bash
git -C "$W1_TEST/upstream" switch -c review/w1-fixes
git -C "$W1_TEST/upstream" apply --check "$W1_PACKAGE/0001-fix-station-free-allocation.patch"
git -C "$W1_TEST/upstream" apply "$W1_PACKAGE/0001-fix-station-free-allocation.patch"
git -C "$W1_TEST/upstream" apply --check "$W1_PACKAGE/0002-fix-early-tx-drop-tid.patch"
git -C "$W1_TEST/upstream" apply "$W1_PACKAGE/0002-fix-early-tx-drop-tid.patch"
git -C "$W1_TEST/upstream" diff --check
git -C "$W1_TEST/upstream" diff --stat
```

Exactly two source files change. The standalone patches are not a Linux 6.18
compatibility port. Do not apply them over a downstream full patch that already
contains these fixes. Stop on conflicts rather than forcing a patch.

### Before hardware testing

Use a matching kernel build environment, including configuration and symbol
versions where applicable. Equal `vermagic` alone does not establish ABI
compatibility. Preserve the old modules and a recovery boot medium. Do not
unload an active vendor Wi-Fi driver on the Aquário: removal is known to fail.

For a controlled comparison, keep board, DTB, power, CPU clocks and kernel
compatibility changes constant; change only the TID fix. Record exact revisions,
module hashes, boot attempts, association cycles, temperature and kernel logs.
Do not publish credentials, personal MAC addresses or private network details.
The isolated series still needs fresh hardware and cross-board validation.

### Instructions for AI assistants

- Check the full base hash before applying patches. Preserve unrelated edits.
- Run the verifier and report its exit status and scope accurately.
- Do not equate source-model success with a kernel or hardware test.
- Do not infer an allocation overflow without expanding `NET_MALLOC`.
- Do not install firmware, alter eMMC, overclock or reboot as a side effect.
- Do not mix these fixes with board-specific tuning in an upstream patch.

## Português

O **TID** é a prioridade do pacote. Antes de classificar o pacote, o driver
pode encontrar na área `skb->cb` um byte deixado pela camada anterior. Usá-lo
como índice sem conferir o limite permite sair de um array de oito elementos.
O segundo patch inicializa esse campo e protege o contador.

O primeiro patch troca o tamanho do ponteiro pelo tamanho da estrutura.
Há um detalhe importante: nesta versão, o alocador arredonda ambos para 128
bytes. Portanto, não se deve anunciar essa mudança como correção de um
segundo overflow comprovado.

Para reproduzir, execute os comandos acima no PC. O verificador trabalha em
uma cópia temporária, exige o commit exato e recusa arquivos versionados
alterados. A saída mostra os tamanhos pedidos e reais, além dos índices que
ultrapassariam o limite do array original antes e depois do patch.

**O teste é um modelo local baseado em trechos reais do fonte.** Não compila
o módulo do kernel nem acessa a box. Testes na Aquário, em outra placa e em
outro kernel precisam de validação própria. Um travamento de CPU/DVFS também
não prova falha no Wi-Fi: investigue cada causa separadamente.

Credits: [CoreELEC/Amlogic](https://github.com/CoreELEC/w1-aml),
[Moxnatiy's Linux 6.18 port](https://github.com/Moxnatiy/tvbox-wifi-fix) and
[devmfc's Debian images](https://github.com/devmfc/debian-on-amlogic).
