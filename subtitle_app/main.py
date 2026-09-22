"""Standalone subtitle desktop application; no Studio server or project required."""
from pathlib import Path
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import captions, paths, probe, subtitle

DATA = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Respect-Subtitles'
paths.set_data_dir(str(DATA))
VIDEOS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v'}


def caption_cli(argv):
    captions.ensure_ffmpeg()
    captions.install()
    from videocaptioner.cli.main import main
    sys.argv = ['videocaptioner'] + argv
    with captions.utf8_subprocess_logs():
        return main() or 0


def cli_command():
    return ([sys.executable] if getattr(sys, 'frozen', False) else
            [sys.executable, str(Path(__file__).resolve())]) + ['caption']


def execute(args, cfg, work, cancel, log):
    target = work / ('process-' + str(time.time_ns()) + '.log')
    with target.open('wb') as stream:
        proc = subprocess.Popen(cli_command() + args + ['--config', str(cfg)],
            stdout=stream, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + 7200
        while proc.poll() is None:
            if cancel.wait(.2) or time.monotonic() > deadline:
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                    capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                proc.wait()
                raise RuntimeError('已停止' if cancel.is_set() else '处理超过两小时')
        if proc.returncode:
            raise RuntimeError(target.read_text(encoding='utf-8', errors='replace')[-2000:])


def process_one(video, imported, output, settings, mode, cancel, log):
    """Every run gets a separate output directory. Never overwrite the source."""
    video = Path(video)
    folder = Path(tempfile.mkdtemp(prefix=video.stem[:50] + '-', dir=output))
    work = folder / '.work'
    work.mkdir()
    cfg = work / 'caption.toml'
    cfg.write_text(subtitle.build_config(settings), encoding='utf-8')
    try:
        srt = folder / (video.stem + (Path(imported).suffix if imported else '.srt'))
        if imported:
            shutil.copy2(imported, srt)
        else:
            log('正在识别：' + video.name)
            pending = work / 'recognized.srt'
            execute(['transcribe', str(video), '-o', str(pending)], cfg, work, cancel, log)
            if not pending.is_file() or not pending.stat().st_size:
                raise RuntimeError('未生成有效字幕文件')
            os.replace(pending, srt)
        if settings['optimize'] or settings['translate']:
            log('正在处理字幕：' + video.name)
            processed = work / 'processed.srt'
            args = ['subtitle', str(srt), '-o', str(processed), '--no-split']
            if not settings['optimize']: args.append('--no-optimize')
            if not settings['translate']: args.append('--no-translate')
            else: args += ['--translator', settings['translate_service'], '--target-language', settings['target_language']]
            execute(args, cfg, work, cancel, log)
            if not processed.is_file() or not processed.stat().st_size:
                raise RuntimeError('字幕处理未产生有效文件')
            srt = folder / (video.stem + '_处理.srt')
            os.replace(processed, srt)
        if cancel.is_set(): raise RuntimeError('已停止')
        if mode != '只导出字幕':
            log('正在生成带字幕视频：' + video.name)
            pending = work / 'subtitled.mp4'
            args = ['synthesize', str(video), '-s', str(srt), '-o', str(pending)]
            if settings['subtitle_mode'] == 'hard' and settings['style']:
                w, h = probe.video_size(str(video)) or (0, 0)
                override = subtitle.style_override(settings['style'], w, h, settings['style_tweak'])
                args += ['--style-override', json.dumps(override, ensure_ascii=False)]
            execute(args, cfg, work, cancel, log)
            if not pending.is_file() or pending.stat().st_size < 512 or not probe.video_size(str(pending)):
                raise RuntimeError('带字幕视频未通过文件校验，字幕文件已保留')
            os.replace(pending, folder / (video.stem + '_SUB.mp4'))
        log('完成：' + str(folder))
        return folder
    finally:
        cfg.unlink(missing_ok=True)  # Temporary configuration can contain an API key.


def gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    root = tk.Tk()
    root.title('Respect 字幕工作台')
    root.geometry('1080x780')
    root.minsize(850, 650)
    style = ttk.Style(root)
    style.theme_use('vista')
    style.configure('.', font=('Microsoft YaHei UI', 10))
    q = queue.Queue()
    cancel = threading.Event()
    state = {'running': False, 'files': [], 'closing': False}
    saved = {}
    try: saved = json.loads((DATA / 'settings.json').read_text(encoding='utf-8'))
    except (OSError, ValueError): pass
    defaults = dict(subtitle.DEFAULTS, optimize=False, translate=False)
    defaults.update(saved)
    variables = {}
    top = ttk.Frame(root, padding=16); top.pack(fill='x')
    ttk.Label(top, text='Respect 字幕工作台', font=('Microsoft YaHei UI', 18, 'bold')).pack(side='left')
    status = tk.StringVar(value='添加视频后开始处理 · 原视频保留')
    ttk.Label(top, textvariable=status).pack(side='left', padx=20)
    tabs = ttk.Notebook(root); tabs.pack(fill='both', expand=True, padx=16)
    files = ttk.Frame(tabs, padding=12); tabs.add(files, text='视频与输出')
    settings_tab = ttk.Frame(tabs, padding=12); tabs.add(settings_tab, text='识别与翻译')
    appearance = ttk.Frame(tabs, padding=12); tabs.add(appearance, text='字幕样式')
    bar = ttk.Frame(files); bar.pack(fill='x')
    tree = ttk.Treeview(files, columns=('video','subtitle'), show='headings', height=10)
    tree.heading('video',text='视频文件');tree.heading('subtitle',text='已有字幕（可选）')
    tree.column('video',width=600);tree.column('subtitle',width=260)
    tree.pack(fill='both',expand=True,pady=10)
    def add(names):
        if state['running']: return
        for name in names:
            p=Path(name)
            if p.suffix.lower() in VIDEOS and str(p) not in state['files']:
                state['files'].append(str(p)); tree.insert('', 'end', values=(str(p), ''))
    ttk.Button(bar,text='添加视频',command=lambda:add(filedialog.askopenfilenames(filetypes=[('视频','*.mp4 *.mov *.mkv *.avi *.webm *.m4v')]))).pack(side='left')
    def folder():
        value=filedialog.askdirectory()
        if value:add(sorted(Path(value).iterdir()))
    ttk.Button(bar,text='添加文件夹',command=folder).pack(side='left',padx=8)
    def attach():
        selected=tree.selection()
        if state['running'] or len(selected)!=1:
            messagebox.showinfo('选择视频','先选中一条视频，再为它指定字幕。');return
        value=filedialog.askopenfilename(filetypes=[('字幕','*.srt *.ass *.vtt')])
        if value:tree.set(selected[0],'subtitle',value)
    ttk.Button(bar,text='指定已有字幕',command=attach).pack(side='left')
    def remove():
        if state['running']:return
        for item in tree.selection():
            state['files'].remove(tree.set(item,'video'));tree.delete(item)
    ttk.Button(bar,text='移除所选',command=remove).pack(side='left',padx=8)
    output=tk.StringVar(value=saved.get('output',str(Path.home()/'Videos'/'Respect字幕')))
    line=ttk.Frame(files);line.pack(fill='x')
    ttk.Label(line,text='输出目录').pack(side='left')
    ttk.Entry(line,textvariable=output).pack(side='left',fill='x',expand=True,padx=8)
    def pickout():
        value=filedialog.askdirectory()
        if value:output.set(value)
    ttk.Button(line,text='选择',command=pickout).pack(side='left')
    mode=tk.StringVar(value='字幕与带字幕视频')
    ttk.Combobox(files,textvariable=mode,values=['字幕与带字幕视频','只导出字幕'],state='readonly').pack(anchor='w',pady=10)
    def field(parent,key,label,values=None,row=0,secret=False,value=None):
        var=tk.StringVar(value=str(defaults.get(key,'') if value is None else value));variables[key]=var
        ttk.Label(parent,text=label).grid(row=row,column=0,sticky='w',pady=5,padx=8)
        widget=ttk.Combobox(parent,textvariable=var,values=values,state='readonly' if values else 'normal',width=54) if values else ttk.Entry(parent,textvariable=var,width=56,show='●' if secret else '')
        widget.grid(row=row,column=1,sticky='ew',pady=5)
        return widget
    field(settings_tab,'asr','识别引擎',['bijian','jianying','faster-whisper','whisper-cpp'],0)
    field(settings_tab,'language','识别语言（auto 自动）',row=1)
    for i,(key,label) in enumerate([('optimize','启用字幕纠错（需要 LLM）'),('translate','启用翻译')],2):
        variables[key]=tk.BooleanVar(value=bool(defaults[key]))
        ttk.Checkbutton(settings_tab,text=label,variable=variables[key]).grid(row=i,column=1,sticky='w',pady=5)
    field(settings_tab,'translate_service','翻译服务',['bing','google','llm'],4)
    field(settings_tab,'target_language','目标语言（如 zh-Hans / en）',row=5)
    field(settings_tab,'llm_api_base','LLM 接口地址',row=6)
    field(settings_tab,'llm_model','LLM 模型',row=7)
    field(settings_tab,'llm_api_key','LLM Key（仅本次运行保留）',row=8,secret=True,value='')
    field(settings_tab,'max_word_count_cjk','中文每行字数',row=9)
    field(settings_tab,'max_word_count_english','英文每行词数',row=10)
    field(settings_tab,'quality','视频压制质量',['low','medium','high'],11)
    ttk.Label(settings_tab,text='bijian / jianying 需要联网；本地 Whisper 引擎需要另备模型。').grid(row=12,columnspan=2,sticky='w',pady=12)
    field(appearance,'subtitle_mode','字幕模式',['hard','soft'],0)
    ttk.Label(appearance,text='hard：烧入画面，样式生效；soft：可关闭字幕轨，外观由播放器决定。').grid(row=1,columnspan=2,sticky='w',pady=5)
    captions.install()
    style_widget=field(appearance,'style','样式（留空用默认）',['']+[s['name'] for s in subtitle.list_styles()],2)
    def importstyle():
        filename=filedialog.askopenfilename(filetypes=[('字幕样式','*.txt *.json')])
        if filename:
            try:
                captions.add_style(Path(filename).name,Path(filename).read_text(encoding='utf-8-sig'))
                style_widget['values']=['']+[s['name'] for s in subtitle.list_styles()]
                variables['style'].set(Path(filename).stem)
            except Exception as exc:messagebox.showerror('样式导入失败',str(exc))
    ttk.Button(appearance,text='导入样式',command=importstyle).grid(row=3,column=1,sticky='w')
    for i, f in enumerate(subtitle.STYLE_FIELDS,4):
        previous=(defaults.get('style_tweak') or {}).get(f['key'],'')
        if f['type']=='bool' and previous!='':previous=str(previous).lower()
        field(appearance,'tweak_'+f['key'],f['label']+'（留空沿用样式）',row=i,
              value=previous,
              values=['','true','false'] if f['type']=='bool' else None)
    logbox=tk.Text(root,height=7,wrap='word',font=('Microsoft YaHei UI',9));logbox.pack(fill='x',padx=16,pady=8)
    bottom=ttk.Frame(root,padding=(16,0,16,12));bottom.pack(fill='x')
    def start():
        if state['running']:return
        try:
            rows=[tree.item(item,'values') for item in tree.get_children()]
            if not rows:raise ValueError('请先添加视频')
            st=dict(defaults)
            st.update({k:v.get() for k,v in variables.items() if not k.startswith('tweak_')})
            for key in ('max_word_count_cjk','max_word_count_english'):st[key]=int(st[key])
            if not 1<=st['max_word_count_cjk']<=100 or not 1<=st['max_word_count_english']<=100:raise ValueError('每行字数须为 1–100')
            st['style_tweak']={}
            for f in subtitle.STYLE_FIELDS:
                value=variables['tweak_'+f['key']].get().strip()
                if value:st['style_tweak'][f['key']]=(value=='true' if f['type']=='bool' else int(value) if f['type']=='int' else float(value) if f['type']=='float' else value)
            if st['subtitle_mode']=='soft':st['style']='';st['style_tweak']={}
            errors=subtitle.config_problems(st)
            if errors:raise ValueError('\n'.join(errors))
            dest=Path(output.get()).expanduser().resolve();dest.mkdir(parents=True,exist_ok=True)
            DATA.mkdir(parents=True,exist_ok=True)
            (DATA/'settings.json').write_text(json.dumps({**{k:v for k,v in st.items() if k!='llm_api_key'},'output':str(dest)},ensure_ascii=False),encoding='utf-8')
        except Exception as exc:messagebox.showerror('请检查设置',str(exc));return
        chosen_mode=mode.get();state['running']=True;cancel.clear();go.config(state='disabled')
        def worker():
            good=bad=0
            try:
                captions.ensure_ffmpeg()
                for index,(video,imported) in enumerate(rows,1):
                    if cancel.is_set():break
                    q.put(('status',f'处理中 {index}/{len(rows)} · {Path(video).name}'))
                    try:process_one(video,imported,dest,st,chosen_mode,cancel,lambda s:q.put(('log',s)));good+=1
                    except Exception as exc:
                        bad+=1;message=str(exc)
                        if st['llm_api_key']:message=message.replace(st['llm_api_key'],'[密钥已隐藏]')
                        q.put(('log',Path(video).name+'：'+message))
                q.put(('status',f'{"已停止" if cancel.is_set() else "处理结束"} · 完成 {good} · 失败 {bad}'))
            finally:q.put(('done',''))
        threading.Thread(target=worker,daemon=True).start()
    go=ttk.Button(bottom,text='开始处理',command=start);go.pack(side='left')
    ttk.Button(bottom,text='停止',command=cancel.set).pack(side='left',padx=8)
    ttk.Button(bottom,text='打开输出目录',command=lambda:os.startfile(output.get()) if Path(output.get()).exists() else None).pack(side='left')
    def pump():
        while not q.empty():
            kind,value=q.get()
            if kind=='status':status.set(value)
            elif kind=='log':logbox.insert('end',value+'\n');logbox.see('end')
            else:
                state['running']=False;go.config(state='normal')
                if state['closing']:root.destroy();return
        root.after(100,pump)
    def close():
        if state['running']:state['closing']=True;cancel.set();status.set('正在停止，请稍候…')
        else:root.destroy()
    root.protocol('WM_DELETE_WINDOW',close)
    pump();root.mainloop()


if __name__ == '__main__':
    if len(sys.argv)>1 and sys.argv[1]=='caption':raise SystemExit(caption_cli(sys.argv[2:]))
    if len(sys.argv)>1 and sys.argv[1]=='--selftest':
        captions.ensure_ffmpeg();captions.install()
        print(json.dumps({'styles':len(subtitle.list_styles()),'ffmpeg':bool(probe.find_ffmpeg())}));raise SystemExit(0)
    gui()
