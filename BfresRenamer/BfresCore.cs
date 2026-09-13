using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using BfresLibrary;
using ZstdSharp;

namespace BfresRenamer
{
    public class RenamedEntry
    {
        public string DictName = "";
        public string OldKey = "";
        public string NewKey = "";
    }

    public class BfresCore
    {
        public const string ResFileSentinel = "__ResFile__";
        public ResFile? ResFile { get; private set; }

        public void Load(string path)
        {
            byte[] compressed = File.ReadAllBytes(path);
            byte[] raw;
            using (var decompressor = new Decompressor())
                raw = decompressor.Unwrap(compressed).ToArray();
            ResFile = new ResFile(new MemoryStream(raw));
        }

        public List<(string DictName, string Key)> CollectEntries()
        {
            var entries = new List<(string, string)>();
            if (ResFile == null) return entries;

            var nameProp = ResFile.GetType().GetProperty("Name") ?? ResFile.GetType().GetProperty("FileName");
            if (nameProp != null && nameProp.PropertyType == typeof(string))
            {
                if (nameProp.GetValue(ResFile) is string currentName && !string.IsNullOrEmpty(currentName))
                    entries.Add((ResFileSentinel, currentName));
            }

            var dictProps = ResFile.GetType().GetProperties()
                .Where(p => p.PropertyType.IsGenericType && p.PropertyType.Name.StartsWith("ResDict"));

            foreach (var prop in dictProps)
            {
                object? dictObj;
                try { dictObj = prop.GetValue(ResFile); } catch { continue; }
                if (dictObj == null) continue;

                dynamic dict = dictObj;
                try { foreach (string key in dict.Keys) entries.Add((prop.Name, key)); }
                catch { }
            }
            return entries;
        }

        /// Benennt alle Einträge um, deren Key exakt einem der übergebenen
        /// alten Namen entspricht (z.B. ModelProjectName UND FmdbName gleichzeitig).
        public List<RenamedEntry> RenameExact(IEnumerable<string> oldNames, string newName)
        {
            var result = new List<RenamedEntry>();
            if (ResFile == null) return result;

            var oldSet = new HashSet<string>(oldNames.Where(n => !string.IsNullOrEmpty(n)), StringComparer.Ordinal);
            if (oldSet.Count == 0) return result;

            foreach (var (dictName, oldKey) in CollectEntries())
            {
                if (!oldSet.Contains(oldKey)) continue;

                RenameEntry(dictName, oldKey, newName);
                result.Add(new RenamedEntry { DictName = dictName, OldKey = oldKey, NewKey = newName });
            }
            return result;
        }

        public void RenameEntry(string dictName, string oldKey, string newKey)
        {
            if (ResFile == null) return;

            if (dictName == ResFileSentinel)
            {
                var nameProp = ResFile.GetType().GetProperty("Name") ?? ResFile.GetType().GetProperty("FileName");
                if (nameProp == null) throw new Exception("Kein Name/FileName-Property auf ResFile gefunden.");
                nameProp.SetValue(ResFile, newKey);
                return;
            }

            var prop = ResFile.GetType().GetProperty(dictName);
            if (prop == null) throw new Exception($"Property '{dictName}' nicht gefunden.");

            dynamic dict = prop.GetValue(ResFile)!;
            dynamic item = dict[oldKey];
            dict.Remove(item);
            try { item.Name = newKey; } catch { }
            dict.Add(newKey, item);
        }

        public void Save(string path)
        {
            if (ResFile == null) return;
            using var outStream = new MemoryStream();
            ResFile.Save(outStream);
            using var compressor = new Compressor(19);
            File.WriteAllBytes(path, compressor.Wrap(outStream.ToArray()).ToArray());
        }
    }
}