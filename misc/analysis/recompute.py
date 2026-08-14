"""Recompute the disputed numbers on the CORRECTED merged dataset, so the paper
can quote figures that reproduce."""
import csv, math, os, hashlib, sys
from collections import defaultdict
D = r"C:\Users\Yaron\Desktop\slbd-server-backup\results-2026-07-24"
BM = r"C:\Users\Yaron\Desktop\Planning\downward-benchmarks"
def load(n): return list(csv.DictReader(open(os.path.join(D, n), encoding="utf-8", errors="replace")))
def gm(xs):
    xs=[x for x in xs if x and x>0]
    return math.exp(sum(math.log(x) for x in xs)/len(xs)) if xs else float("nan")
def med(xs):
    xs=sorted(x for x in xs if x is not None); return xs[len(xs)//2] if xs else float("nan")
def f(r,k):
    try:
        v=float(r[k]); return v if v>0 else None
    except Exception: return None
def signtest(a,b):
    n=a+b; k=min(a,b)
    return min(1.0, 2*sum(math.comb(n,i) for i in range(k+1))/2**n) if n else 1.0
grand=load("symk-selector-grand.csv"); fix=load("symk-selector-grand-fix7.csv")
fixed={(r["domain"],r["problem"]) for r in fix}
M=defaultdict(dict)
for r in grand:
    if (r["domain"],r["problem"]) not in fixed: M[(r["domain"],r["problem"])][r["config"]]=r
for r in fix: M[(r["domain"],r["problem"])][r["config"]]=r
sv=lambda m,c: m.get(c,{}).get("solved")=="1"

print("### 1. floortile: which domain gives the 20/20 claim?")
for dom in sorted({k[0] for k in M if k[0].startswith("floortile")}):
    sub=[m for k,m in M.items() if k[0]==dom and sv(m,"off") and sv(m,"on")]
    r=[f(m["on"],"wall")/f(m["off"],"wall") for m in sub if f(m["off"],"wall") and f(m["on"],"wall")]
    if r:
        sl=sum(1 for x in r if x>1)
        print("   %-26s n=%2d median=%.3f slower %d/%d  signtest p=%.2g Bonf(66)=%.3g"%(
            dom,len(r),med(r),sl,len(r),signtest(sl,len(r)-sl),min(1,signtest(sl,len(r)-sl)*66)))

print("\n### 2. regression to the mean, CORRECTED data, both thresholds")
common=[m for m in M.values() if sv(m,"off") and sv(m,"on") and f(m["off"],"wall") and f(m["on"],"wall")]
for T in (10,60,300):
    out=[]
    for lab,sel in (("by-off",lambda m:f(m["off"],"wall")>T),
                    ("by-on",lambda m:f(m["on"],"wall")>T),
                    ("symmetric",lambda m:min(f(m["off"],"wall"),f(m["on"],"wall"))>T)):
        sub=[m for m in common if sel(m)]
        r=[f(m["on"],"wall")/f(m["off"],"wall") for m in sub]
        out.append("%s=%.3f(n=%d,%.0f%% faster)"%(lab,gm(r),len(r),100.0*sum(1 for x in r if x<1)/len(r)))
    print("   T=%4ds  %s"%(T," ".join(out)))

print("\n### 3. noise floor on an explicit basis")
pairs=[(f(m["selector"],"search_time"),f(m["off"],"search_time")) for m in M.values()
       if sv(m,"off") and sv(m,"selector") and (m["selector"].get("picked") or "") in ("off","presolve")
       and f(m["selector"],"search_time") and f(m["off"],"search_time")]
lr=[math.log(a/b) for a,b in pairs]
sd=math.sqrt(sum(x*x for x in lr)/len(lr))
big=sum(1 for a,b in pairs if abs(a/b-1)>0.20)
print("   identical-code-path pairs n=%d  sd(log)=%.3f  >20%% apart: %.0f%%"%(len(pairs),sd,100.0*big/len(pairs)))
# coverage drift between the two independent full runs of stock (excluding fix7 domains)
ab=load("symk-co-grand-ab.csv")
A=defaultdict(dict)
for r in ab: A[(r["domain"],r["problem"])][r["config"]]=r
fixdoms={k[0] for k in fixed}
covA=sum(1 for k,m in A.items() if k[0] not in fixdoms and m.get("off",{}).get("solved")=="1")
covB=sum(1 for k,m in M.items() if k[0] not in fixdoms and sv(m,"off"))
print("   stock coverage, two independent runs (excl. repaired domains): %d vs %d -> drift %d"%(covA,covB,abs(covA-covB)))

print("\n### 4. TR-probe deviating-pick accuracy, corrected data")
dev=[m for m in M.values() if (m.get("selector",{}).get("picked") or "")=="on"
     and sv(m,"off") and sv(m,"on") and f(m["off"],"search_time") and f(m["on"],"search_time")]
right=sum(1 for m in dev if f(m["on"],"search_time")<f(m["off"],"search_time"))
print("   n=%d right=%d (%.1f%%) p=%.3g"%(len(dev),right,100.0*right/len(dev),signtest(right,len(dev)-right)))

print("\n### 5. restart-schedule inventory, corrected data")
resc=[m for m in M.values() if not sv(m,"off") and sv(m,"on") and f(m["on"],"wall")]
for T in (600,900):
    print("   stock-unsolved rescued by 'on' within %ds: %d"%(T,sum(1 for m in resc if f(m["on"],"wall")<=T)))
print("   stock-solved instances needing >900s: %d"%sum(
    1 for m in M.values() if sv(m,"off") and f(m["off"],"wall") and f(m["off"],"wall")>900))

print("\n### 6. duplicates restricted to the 66 evaluated domains")
doms={k[0] for k in M}
h2f=defaultdict(list)
for dom in doms:
    p=os.path.join(BM,dom)
    if not os.path.isdir(p): continue
    for fn in os.listdir(p):
        if not fn.endswith(".pddl") or "domain" in fn: continue
        try: h=hashlib.md5(open(os.path.join(p,fn),"rb").read()).hexdigest()
        except Exception: continue
        h2f[h].append((dom,fn))
cross={h:v for h,v in h2f.items() if len({d for d,_ in v})>1}
print("   cross-domain duplicate sets: %d ; instances involved: %d (of %d scanned)"%(
    len(cross),sum(len(v) for v in cross.values()),sum(len(v) for v in h2f.values())))
