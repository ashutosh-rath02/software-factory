import json, os, select, subprocess, sys, time
p = subprocess.Popen(['codex','app-server','--listen','stdio://'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
rows=[]
def send(frame):
    raw=json.dumps(frame,separators=(',',':'))
    p.stdin.write(raw+'\n');p.stdin.flush();rows.append({'direction':'client','raw':raw})
def recv(pred, timeout=15):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        ready,_,_=select.select([p.stdout],[],[],max(0,end-time.monotonic()))
        if not ready: break
        line=p.stdout.readline().strip()
        if not line: break
        rows.append({'direction':'server','raw':line})
        frame=json.loads(line)
        if pred(frame): return frame
    raise RuntimeError('missing expected frame')
try:
    send({'id':1,'method':'initialize','params':{'clientInfo':{'name':'factory-capture','version':'0.1'}}})
    recv(lambda f:f.get('id')==1)
    send({'method':'initialized','params':{}})
    send({'id':2,'method':'thread/start','params':{'model':'gpt-6-sol','cwd':os.getcwd(),'sandbox':'danger-full-access','approvalPolicy':'never'}})
    started=recv(lambda f:f.get('id')==2)
    tid=started['result']['thread']['id']
    send({'id':3,'method':'thread/goal/set','params':{'threadId':tid,'objective':'SRV-FEAT-593 handover to QA','status':'active','tokenBudget':500000}})
    recv(lambda f:f.get('id')==3)
    send({'id':4,'method':'thread/compact/start','params':{'threadId':tid}})
    recv(lambda f:f.get('id')==4)
    send({'id':5,'method':'thread/goal/set','params':{'threadId':'00000000-0000-0000-0000-000000000000','objective':'invalid test','status':'active','tokenBudget':500000}})
    recv(lambda f:f.get('id')==5)
finally:
    p.terminate()
    try:p.wait(timeout=3)
    except subprocess.TimeoutExpired:p.kill();p.wait()
    print(json.dumps({'codex_version':'0.162.0-alpha.17.2','records':rows},indent=2))
