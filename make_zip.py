import os, zipfile
p='ai-dotnet-upgrader-langgraph'; z=f'{p}.zip'
with zipfile.ZipFile(z,'w',zipfile.ZIP_DEFLATED) as zipf:
    for r,_,fs in os.walk(p):
        for f in fs:
            zipf.write(os.path.join(r,f), os.path.relpath(os.path.join(r,f), p))
print('✅ Created', z)
