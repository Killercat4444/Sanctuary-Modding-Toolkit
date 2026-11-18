// Sanctuary Studio generic standalone support. No faction/project IDs are embedded.
// BepInEx 5 preloader; patches the in-memory game assembly, never the file on disk.
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using Mono.Cecil;
using Mono.Cecil.Cil;
using Newtonsoft.Json.Linq;

public static class SanctuaryStudioCachePatcher {
    public static IEnumerable<string> TargetDLLs { get { return new[] { "Trebuchet.dll" }; } }
    public static void Patch(AssemblyDefinition assembly) {
        var module=assembly.MainModule;
        var cache=module.GetType("EM.Lua.FilesCache");
        var create=cache==null ? null : cache.Methods.SingleOrDefault(m=>m.Name=="CreateFileCache" && m.Parameters.Count==0 && m.HasBody);
        if(create==null || create.ReturnType.FullName!="System.Void" || !cache.Fields.Any(f=>f.Name=="pathToFileContents") || !cache.Fields.Any(f=>f.Name=="directoryToSubFolderNames")) {
            Console.WriteLine("[Sanctuary Studio] Unsupported game cache layout. Standalone content was NOT activated; use an updated exporter or MM.");return;
        }
        Hook(create,module.ImportReference(typeof(SanctuaryStudioStandalone).GetMethod("Apply")),false);
        Console.WriteLine("[Sanctuary Studio] Generic unit/art Lua-cache hook installed. New factions require MM.");
    }
    static void Hook(MethodDefinition method,MethodReference target,bool instance) {
        if(method.ReturnType.FullName!="System.Void")throw new InvalidOperationException("Unsupported return type: "+method.FullName);
        if(method.Body.Instructions.Any(i=>i.Operand is MethodReference && ((MethodReference)i.Operand).FullName==target.FullName))return;
        var il=method.Body.GetILProcessor();
        foreach(var ret in method.Body.Instructions.Where(i=>i.OpCode==OpCodes.Ret).ToArray()) {
            ret.OpCode=instance ? OpCodes.Ldarg_0 : OpCodes.Call;ret.Operand=instance ? null : target;
            var cursor=ret;
            if(instance) {var call=Instruction.Create(OpCodes.Call,target);il.InsertAfter(cursor,call);cursor=call;}
            il.InsertAfter(cursor,Instruction.Create(OpCodes.Ret));
        }
    }
}

public static class SanctuaryStudioStandalone {
    static object lastCache;
    static bool applying;
    public static void Apply() {
        if(applying)return;applying=true;
        try {
            string engine=AppDomain.CurrentDomain.BaseDirectory;
            string packages=Path.Combine(engine,"StudioStandalone");
            if(!Directory.Exists(packages))return;
            var game=AppDomain.CurrentDomain.GetAssemblies().Single(a=>a.GetName().Name=="Trebuchet");
            var type=game.GetType("EM.Lua.FilesCache",true);var flags=BindingFlags.Static|BindingFlags.Public|BindingFlags.NonPublic;
            var field=type.GetField("pathToFileContents",flags);var indexField=type.GetField("directoryToSubFolderNames",flags);
            var cache=(IDictionary)field.GetValue(null);if(Object.ReferenceEquals(lastCache,cache))return;
            string luaRoot=Path.GetFullPath(Path.Combine(engine,"LJ","lua"));
            var pending=new SortedDictionary<string,byte[]>(StringComparer.OrdinalIgnoreCase);
            var appends=new SortedDictionary<string,List<byte[]>>(StringComparer.OrdinalIgnoreCase);
            string hash;
            using(var stream=File.OpenRead(Path.Combine(engine,"Sanctuary_Data","Managed","Trebuchet.dll")))
            using(var sha=SHA256.Create())hash=BitConverter.ToString(sha.ComputeHash(stream)).Replace("-","").ToLowerInvariant();
            foreach(string directory in Directory.GetDirectories(packages).OrderBy(p=>p,StringComparer.OrdinalIgnoreCase)) {
                string descriptor=Path.Combine(directory,"runtime.json");string zip=Path.Combine(directory,"cache.zip");
                if(!File.Exists(descriptor) || !File.Exists(zip))continue;
                var config=JObject.Parse(File.ReadAllText(descriptor));
                if((string)config["format"]!="Sanctuary Studio standalone 1" || (string)config["gameAssemblySha256"]!=hash)
                    throw new InvalidDataException("Standalone package targets a different game build: "+directory+". Re-export after updating the game.");
                if((config["factions"] as JArray)?.Count>0)
                    throw new InvalidDataException("New standalone faction registration is unsupported; use MM for dynamic faction slots: "+directory);
                using(var archive=ZipFile.OpenRead(zip))foreach(var entry in archive.Entries) {
                    const string head="files/LJ/lua/";
                    if(!entry.FullName.StartsWith(head,StringComparison.Ordinal) || entry.FullName.EndsWith("/"))continue;
                    string relative=entry.FullName.Substring(head.Length);bool append=relative.StartsWith("append/",StringComparison.OrdinalIgnoreCase);
                    if(append)relative=relative.Substring(7);
                    string path=Path.GetFullPath(Path.Combine(luaRoot,relative.Replace('/',Path.DirectorySeparatorChar)));
                    if(!path.StartsWith(luaRoot+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("Lua path escapes cache root.");
                    byte[] bytes;using(var input=entry.Open())using(var buffer=new MemoryStream()){input.CopyTo(buffer);bytes=buffer.ToArray();}
                    if(append) {List<byte[]> list;if(!appends.TryGetValue(path,out list))appends[path]=list=new List<byte[]>();list.Add(bytes);}
                    else {if(pending.ContainsKey(path))throw new InvalidDataException("Conflicting standalone Lua path: "+relative);pending[path]=bytes;}
                }
            }
            var valueType=field.FieldType.GetGenericArguments()[1];var ctor=valueType.GetConstructors().Single(c=>c.GetParameters().Length==2 && c.GetParameters()[0].ParameterType==typeof(byte[]) && c.GetParameters()[1].ParameterType.IsEnum);
            var toArray=valueType.GetMethod("ToArray",Type.EmptyTypes);var dispose=valueType.GetMethod("Dispose",Type.EmptyTypes);
            object persistent=Enum.Parse(ctor.GetParameters()[1].ParameterType,"Persistent");
            foreach(var entry in appends) {
                byte[] original;
                if(!pending.TryGetValue(entry.Key,out original)) {
                    if(!cache.Contains(entry.Key))throw new InvalidDataException("Append target absent from game cache: "+entry.Key);
                    original=(byte[])toArray.Invoke(cache[entry.Key],null);
                }
                foreach(byte[] patch in entry.Value)original=Merge(original,patch);
                pending[entry.Key]=original;
            }
            // Allocate everything before changing the live dictionary.
            var allocated=new List<KeyValuePair<string,object>>();
            try {foreach(var entry in pending)allocated.Add(new KeyValuePair<string,object>(entry.Key,ctor.Invoke(new object[]{entry.Value,persistent})));}
            catch {foreach(var entry in allocated)dispose.Invoke(entry.Value,null);throw;}
            var index=(IDictionary<string,HashSet<string>>)indexField.GetValue(null);
            foreach(var entry in allocated) {
                object old=cache.Contains(entry.Key)?cache[entry.Key]:null;cache[entry.Key]=entry.Value;if(old!=null)dispose.Invoke(old,null);
                string child=Path.GetDirectoryName(entry.Key);
                while(!String.Equals(child,luaRoot,StringComparison.OrdinalIgnoreCase)) {
                    string parent=Path.GetDirectoryName(child);HashSet<string> children;
                    if(!index.TryGetValue(parent,out children))index[parent]=children=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                    children.Add(Path.GetFileName(child));child=parent;
                }
            }
            lastCache=cache;
            Console.WriteLine("[Sanctuary Studio] Cached "+pending.Count+" unit/art Lua files. Faction slots unchanged.");
        } catch(Exception e) {
            Console.WriteLine("[Sanctuary Studio] Standalone activation stopped BEFORE match load: "+e+". Check the package/game version and use MM for conflicting mods.");
        } finally {applying=false;}
    }
    static byte[] Merge(byte[] source,byte[] patch) {
        string text=Encoding.UTF8.GetString(source).TrimEnd('\r','\n');string addition=Encoding.UTF8.GetString(patch).Trim('\r','\n');
        var lines=text.Split(new[]{'\n'});int insert=lines.Length;
        for(int i=lines.Length-1;i>=0;i--)if(lines[i].StartsWith("return ") || lines[i].TrimEnd(new[]{'\r'})=="return") {insert=i;break;}
        return Encoding.UTF8.GetBytes(String.Join("\n",lines.Take(insert))+"\n"+addition+"\n"+String.Join("\n",lines.Skip(insert))+"\n");
    }
}
