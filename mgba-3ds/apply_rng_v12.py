from pathlib import Path
p=Path('/tmp/bridge/overlay/src/platform/3ds')
c=p/'pmgb_bridge.c'
h=p/'pmgb_protocol.h'
def one(s, a, b):
    n=s.count(a)
    if n!=1: raise RuntimeError(f'Expected one {a[:80]!r}, got {n}')
    return s.replace(a,b,1)
s=c.read_text()
t=h.read_text()
t=one(t,'#define PMGB_RELEASE 5','#define PMGB_RELEASE 5\n#define PMGB_SET_RNG_SEED 6')
s=one(s,'static uint32_t pmgbPreviousFrame = 0;', '''static uint32_t pmgbPreviousFrame = 0;
/* Only a fixed Ruby rev1 RNG word is writable, with replay-safe ACK. */
static bool pmgbLastSeedValid = false;
static uint32_t pmgbLastSeedSeq, pmgbLastSeedArg, pmgbLastSeedOld, pmgbLastSeedNew;
static uint32_t pmgbLastSeedHost;
static uint16_t pmgbLastSeedPort;
#define PMGB_RUBY_REV1_RNG 0x03004818u
#define PMGB_GMAIN_CALLBACK2 0x03001774u
#define PMGB_GTASKS_TASK0_FUNC 0x03004B20u
#define PMGB_GTASKS_TASK0_FLAGS 0x03004B24u
#define PMGB_BATTLE_TYPE_FLAGS 0x020239F8u
#define PMGB_CB_STARTER_SELECT 0x0810A13Du
#define PMGB_TASK_STARTER_SELECT 0x0810A199u
static bool pmgbCanSetRngSeed(struct mCore* core, const struct mGameInfo* info) {
    if (!core || !core->busRead32 || !core->busWrite32 || !core->busRead8 ||
        pmgbPanicked || pmgbKeys || !info || memcmp(info->code, "AXVE", 4) ||
        info->version != 1) return false;
    return core->busRead32(core, PMGB_GMAIN_CALLBACK2) == PMGB_CB_STARTER_SELECT &&
           core->busRead32(core, PMGB_GTASKS_TASK0_FUNC) == PMGB_TASK_STARTER_SELECT &&
           (core->busRead8(core, PMGB_GTASKS_TASK0_FLAGS) & 1u) &&
           core->busRead32(core, PMGB_BATTLE_TYPE_FLAGS) == 0;
}
''')
s=one(s,'    pmgbPanicked = false;\n    pmgbRelease();','    pmgbPanicked = false;\n    pmgbLastSeedValid = false;\n    pmgbRelease();')
s=one(s,'void pmgbBridgeGameLoaded(struct mCore* core) {\n    pmgbRelease();','void pmgbBridgeGameLoaded(struct mCore* core) {\n    pmgbRelease();\n    pmgbLastSeedValid = false;')
s=one(s,'void pmgbBridgeGameUnloaded(void) {\n    pmgbRelease();','void pmgbBridgeGameUnloaded(void) {\n    pmgbLastSeedValid = false;\n    pmgbRelease();')
s=one(s,'const char ident[] = "Pokebot-mGBA v1";','const char ident[] = "Pokebot-mGBA v12 RNG";')
s=one(s,'''    } else {
        status = PMGB_BAD_COMMAND;
    }

    pmgb_write_header''','''    } else if (cmd == PMGB_SET_RNG_SEED) {
        struct mGameInfo info;
        if (!pmgbLoaded || !pmgbIsRubySapphire(core, &info) ||
            memcmp(info.code, "AXVE", 4) || info.version != 1) status = PMGB_NO_GAME;
        else if (requested || !arg || pmgbPanicked) status = PMGB_BAD_ARG;
        else if (pmgbLastSeedValid && seq == pmgbLastSeedSeq &&
                 sender.sin_addr.s_addr == pmgbLastSeedHost &&
                 sender.sin_port == pmgbLastSeedPort) {
            if (arg != pmgbLastSeedArg) status = PMGB_BAD_ARG;
            else {
                pmgb_put32(response + PMGB_HEADER_SIZE, pmgbLastSeedOld);
                pmgb_put32(response + PMGB_HEADER_SIZE + 4, pmgbLastSeedNew);
                replyLen = 8;
            }
        } else if (!pmgbCanSetRngSeed(core, &info)) status = PMGB_BAD_ARG;
        else {
            uint32_t old = core->busRead32(core, PMGB_RUBY_REV1_RNG);
            core->busWrite32(core, PMGB_RUBY_REV1_RNG, arg);
            uint32_t now = core->busRead32(core, PMGB_RUBY_REV1_RNG);
            if (now != arg) status = PMGB_BAD_ARG;
            else {
                pmgbLastSeedSeq = seq;
                pmgbLastSeedArg = arg;
                pmgbLastSeedOld = old;
                pmgbLastSeedNew = now;
                pmgbLastSeedHost = sender.sin_addr.s_addr;
                pmgbLastSeedPort = sender.sin_port;
                pmgbLastSeedValid = true;
                pmgb_put32(response + PMGB_HEADER_SIZE, old);
                pmgb_put32(response + PMGB_HEADER_SIZE + 4, now);
                replyLen = 8;
            }
        }
    } else {
        status = PMGB_BAD_COMMAND;
    }

    pmgb_write_header''')
c.write_text(s)
h.write_text(t)
print('Applied v12 RNG write gate to checked v1 source')
