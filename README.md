# WonderDuplicationTool

Things u need for it to Work:
Python 3.12 and
  following Packages:
    PyQt5,
    zstandard,
    sarc,
    oead
    
.NET Desktop Runtime


BACKUP ur mod, im not accountable for things getting corrupted or smth with this tool.

Its a Tool, which allows the user too Duplicate Mario Wonder Actors more simpler. Its a Gui based Application, so dunno how much i should explain here, its more or less self Explanitory. But still, here is a short rundown on how to use it:

After starting it go to Settings and Set every Path

For the Source RomFS set the Location from the Game Dump

For the Mod-ROmfs set the location to your romfs (set it to the romfs folder itself)

For the ActorInfo set it to the ActorInfo... file inside the RSDB folder (optional)

For the GameActorInfo set it to the GameActor... file inside the RSDB folder (optional)

For the RSTB Generator set it to the RSTB generator inside your romfs Folder (optional)

Ignore the TagDatabase, its not implemented yet

Language, Theme, Font, Font Size is self explanitory

High DPI is usefull for Monitors with a high res

Batch Mode allows you to make Multiple Copies at once from one Actor, either with ";" or Linebreaks

After that click on the "Clone Actor" tab and seach up for the Actor u want to Clone

U can also Clone Actors which u have already created, via the top right button "Orignial" and change it to Mod

Beneath that u can put the name of your Duped Actor, it SHOULD check if the name already is used in the mod, but still try to remember which actornames youve used

U can hover over the 4 checkboxes and see a tooltip which explains what it does.

-"Adjust ActorEngie" renames the ActorEngine file inside the pack file.

-"Adjust ModelInfo Refernces" copies the BFRES.zs file, renames the BFRES and the Model to the Name which is set in the "New Actor Name" , renames the modelinfo inside the engine file and inside the Component\Modelinfo sets the RSDB and ModelProjectName to the value set in the "New Actor Name"

-"Adjust RSDB Entries" Adds the Entry into both Tables, if they are set in the Settings (it copies the Entries from the OG one and change the Fmdb, ModelProjectName and the Rowid)

-"Update TagDatabase" not implemented

Click "Clone Actor" if u Think u got everysetting u want

If u have the RSTB.exe defined in the settings, u can run it here.

In the Pack Editor, u can THEORETICALL edit .bfres (which u shouldnt) .bgmyl and .byml files, but i highly recommend to use STB for it.
