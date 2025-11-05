import os, tempfile, zipfile, re
def save_uploaded_zip(u):
    t=tempfile.mkdtemp(); p=os.path.join(t,'upload.zip'); open(p,'wb').write(u.read())
    import zipfile; zipfile.ZipFile(p).extractall(t); return t
def create_upgraded_zip(root, updates, target):
    zpath=os.path.join(tempfile.gettempdir(),f'AI_Upgraded_{target}.zip')
    with zipfile.ZipFile(zpath,'w',zipfile.ZIP_DEFLATED) as z:
        for r,_,fs in os.walk(root):
            for f in fs:
                src=os.path.join(r,f); rel=os.path.relpath(src,root)
                if rel in updates: z.writestr(rel,updates[rel])
                else:
                    if rel.endswith('.csproj'):
                        orig=open(src).read(); upd=re.sub(r'<TargetFramework>.*?</TargetFramework>',f'<TargetFramework>{target}</TargetFramework>',orig)
                        z.writestr(rel,upd)
                    else: z.write(src,rel)
    return zpath
def extract_diffs_from_markdown(t): return []
