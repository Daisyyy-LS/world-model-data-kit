import os, json, urllib.request as u
TOKEN = os.popen('gh auth token').read().strip()
opener = u.build_opener(u.ProxyHandler({'http':'http://127.0.0.1:51903','https':'http://127.0.0.1:51903'}))

def api(path, method='GET', data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = u.Request('https://api.github.com'+path, data=body, method=method,
        headers={'Authorization':'Bearer '+TOKEN,'Accept':'application/vnd.github+json',
                 'X-GitHub-Api-Version':'2022-11-28','User-Agent':'wm-kit'})
    with opener.open(req, timeout=90) as r:
        return json.load(r)

REPO='/repos/Daisyyy-LS/world-model-data-kit'
head = api(REPO+'/commits/main')
parent = head['sha']
base_tree = head['commit']['tree']['sha']
print('remote HEAD:', parent[:8])

# 本地所有要提交的文件（排除 .git 与 __pycache__）
files = []
for root, dirs, names in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__')]
    for n in names:
        if n.endswith('.pyc'): continue
        p = os.path.relpath(os.path.join(root, n), '.').replace('\\', '/')
        files.append(p)
files.sort()
print('local files:', len(files))

# 为每个文件创建 blob
tree_entries = []
for p in files:
    data = open(p, 'rb').read()
    # 二进制 or utf-8 文本都走 base64 blob
    import base64
    blob = api(REPO+'/git/blobs', 'POST', {'content': base64.b64encode(data).decode(), 'encoding': 'base64'})
    tree_entries.append({'path': p, 'mode': '100644', 'type': 'blob', 'sha': blob['sha']})
    print('  blob', p[:50])

# 用 base_tree + 全部文件条目构建新 tree（覆盖式，保证与本地一致）
tree = api(REPO+'/git/trees', 'POST', {'base_tree': base_tree, 'tree': tree_entries})
print('tree:', tree['sha'][:8])

msg = ('feat: v0.3 与测评台 v7 对齐 - 9维/真实样本/跨仓库对接\n\n'
       '- score.py 8→9 维，新增 crossseg（跨段一致性）维度与计分/校验\n'
       '- 新增真实样本 hunyuanworld_mirror_room.json（腾讯模型真实跑通，9维N/A，元信息可追溯）\n'
       '- 新增 from_playground.py：测评台记录桥接成 clip 骨架，示意记录默认剔除\n'
       '- 新增跨段示意样本 clip_C_crossseg.json；任务卡 3→4\n'
       '- 全文档更新：README/SKILL/schema/hy-nomas/literature，真实跑通状态与诚实边界同步\n'
       '- 加 .gitignore')
commit = api(REPO+'/git/commits', 'POST', {
    'message': msg, 'tree': tree['sha'], 'parents': [parent],
    'author': {'name':'Daisyyy-LS','email':'Daisyyy-LS@users.noreply.github.com'},
    'committer': {'name':'Daisyyy-LS','email':'Daisyyy-LS@users.noreply.github.com'}
})
print('commit:', commit['sha'][:8])

api(REPO+'/git/refs/heads/main', 'PATCH', {'sha': commit['sha'], 'force': False})
print('ref updated ->', commit['sha'])
