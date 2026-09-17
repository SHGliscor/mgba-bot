#!/usr/bin/env python3
import json, os
from pathlib import Path

STATES=("Target","Allowed","Blocked")

def _path():
    base=os.environ.get("APPDATA")
    root=Path(base) if base else Path.home()/"AppData"/"Roaming"
    p=root/"Pokebot3DS-CFW"/"Gen3"/"rs_hunt_policy.json"
    p.parent.mkdir(parents=True,exist_ok=True)
    return p

class HuntPolicy:
    def __init__(self):
        self.path=_path(); self.data={"schema_version":1,"rules":{}}
        try:
            loaded=json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded,dict): self.data.update(loaded)
        except Exception: pass
    @staticmethod
    def context_key(code, group, num, method): return f"{code}|{int(group)}|{int(num)}|{method}"
    def state(self, code, group, num, method, species_id):
        key=self.context_key(code,group,num,method)
        return (self.data.get("rules",{}).get(key,{}) or {}).get(str(int(species_id)),"Allowed")
    def set_state(self, code, group, num, method, species_id, state):
        if state not in STATES: raise ValueError(state)
        key=self.context_key(code,group,num,method)
        self.data.setdefault("rules",{}).setdefault(key,{})[str(int(species_id))]=state
        self.save()
    def set_many(self, code, group, num, method, species_ids, state):
        if state not in STATES: raise ValueError(state)
        key=self.context_key(code,group,num,method); rules=self.data.setdefault("rules",{}).setdefault(key,{})
        for sid in species_ids: rules[str(int(sid))]=state
        self.save()
    def clear_context(self, code, group, num, method):
        self.data.setdefault("rules",{}).pop(self.context_key(code,group,num,method),None); self.save()
    def targets(self, code, group, num, method):
        key=self.context_key(code,group,num,method)
        return {int(k) for k,v in (self.data.get("rules",{}).get(key,{}) or {}).items() if v=="Target"}
    def save(self):
        tmp=self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data,indent=2,sort_keys=True),encoding="utf-8")
        os.replace(tmp,self.path)
