#!/usr/bin/env python3
import threading, time
from PySide6.QtCore import QThread, Signal
from gen3bot.core.bridge import Bridge
from gen3bot.games.rs import ADDR, detect_profile, player_state
from gen3bot.pokemon.pk3 import decode_mon, apply_species_metadata
from gen3bot.data.rs_world import RSWorldDatabase, effective_encounter_percent, wurmple_evolution
from gen3bot.data.terrain import TerrainReader

WHITE_FLUTE_FLAG=0x84D
BLACK_FLUTE_FLAG=0x84E
CLEANSE_TAG_ITEM=190
FLAGS_OFFSET=0x1220

class WorldStatePoller(QThread):
    state=Signal(dict)
    world_ready=Signal(dict)
    log=Signal(str)
    def __init__(self, ip, method="land", interval=1.5):
        super().__init__(); self.ip=ip; self.method=method; self.interval=float(interval); self._stop=threading.Event(); self._rebuild=False
    def request_stop(self): self._stop.set()
    def set_method(self,method): self.method=method
    def request_world_rebuild(self): self._rebuild=True
    def _flag(self,b,flag):
        raw=b.read(ADDR["gSaveBlock1"]+FLAGS_OFFSET+flag//8,1)[0]
        return bool(raw & (1<<(flag%8)))
    def run(self):
        bridge=None; db=None; profile=None; built=False
        while not self._stop.is_set():
            try:
                if bridge is None:
                    bridge=Bridge(self.ip,timeout=0.6); bridge.cmd("PING"); profile=detect_profile(bridge); db=RSWorldDatabase(bridge,profile)
                ps=player_state(bridge); group,num=int(ps["map_group"]),int(ps["map_num"])
                method=self.method or "land"
                mapdata=db.map_data(group,num)
                party=[]; count=min(6,bridge.read(ADDR["gPlayerPartyCount"],1)[0])
                for i in range(count):
                    raw=bridge.read(ADDR["gPlayerParty"]+i*100,100); mon=decode_mon(raw)
                    if not mon: continue
                    meta=db.species_meta(mon["species_id"]); mon=apply_species_metadata(mon,meta,db.item_name(mon.get("held_item_id",0)))
                    mon["wurmple_evolution"]=wurmple_evolution(mon["pid"],mon["species_id"])
                    party.append(mon)
                white=self._flag(bridge,WHITE_FLUTE_FLAG)
                black=self._flag(bridge,BLACK_FLUTE_FLAG)
                avatar=bridge.read(ADDR["gPlayerAvatar"],1)[0]; bike=bool(avatar & 0x06)
                lead_ability=(party[0].get("ability") if party else "") or ""
                illuminate=lead_ability.lower()=="illuminate"; stench=lead_ability.lower()=="stench"
                cleanse=bool(party and int(party[0].get("held_item_id",0))==CLEANSE_TAG_ITEM)
                base=db.method_base_rate(group,num,method)
                eff=effective_encounter_percent(base,white_flute=white,black_flute=black,illuminate=illuminate,bike=bike,stench=stench,cleanse_tag=cleanse)
                species=[]
                for row in db.method_species(group,num,method):
                    r=dict(row); r.update(db.species_meta(r["species_id"])); species.append(r)
                terrain=None
                if ps.get("x") is not None and ps.get("y") is not None:
                    try:
                        tr=TerrainReader(bridge, profile); terrain={"current":tr.tile(ps["x"],ps["y"]),"edges":tr.edge_mask(ps["x"],ps["y"],"water" if method=="water" else "land")}
                    except Exception as e: terrain={"error":str(e)}
                self.state.emit({"profile":profile,"player":ps,"map":mapdata,"method":method,"species":species,"party":party,"white_flute":white,"black_flute":black,"illuminate":illuminate,"stench":stench,"cleanse_tag":cleanse,"bike":bike,"base_rate":base,"base_percent":effective_encounter_percent(base),"effective_percent":eff,"terrain":terrain})
                if (not built or self._rebuild) and not self._stop.is_set():
                    # Build the complete version-specific world DB once, off the UI thread.
                    payload=db.scan_all(stop_requested=self._stop.is_set)
                    if self._stop.is_set():
                        break
                    path=db.save_cache(payload); built=True; self._rebuild=False
                    self.world_ready.emit({"path":str(path),"maps":len(payload.get("maps",[])),"game":profile.get("name"),"world":payload})
                self._stop.wait(self.interval)
            except Exception as e:
                self.log.emit(f"Live RS reader: {e}")
                bridge=None; db=None; profile=None
                self._stop.wait(2.0)
