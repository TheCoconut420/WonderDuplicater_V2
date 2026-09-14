# WonderDuplicationTool

Things u need for it to Work:
Python 3.12 and
  following Packages:
    PyQt5,
    zstandard,
    sarc,
    oead,
    numpy,
    matplotlib,
    ainb

DotNET Desktop Runtime

BACKUP ur mod, im not accountable for things getting corrupted or smth with this tool.

Its a Tool, which allows the user too Duplicate Mario Wonder Actors more simpler, and now also to edit the more common stuff on them (Hitboxes, Damage Reactions, Physics) without having to dig through the raw files by hand. Its a Gui based Application, so dunno how much i should explain here, its more or less self Explanitory. But still, here is a short rundown on how to use it:

After starting it go to Settings and Set every Path

For the Source RomFS set the Location from the Game Dump

For the Mod-Romfs set the location to your romfs (set it to the romfs folder itself)

For the ActorInfo set it to the ActorInfo... file inside the RSDB folder (optional)

For the GameActorInfo set it to the GameActor... file inside the RSDB folder (optional)

For the RSTB Generator set it to the RSTB generator inside your romfs Folder (optional)

Ignore the RSTBL and TagDatabase stuff, not implemented yet

Language, Theme, Font, Font Size is self explanitory

High DPI is usefull for Monitors with a high res

Batch Mode allows you to make Multiple Copies at once from one Actor, either with ";" or Linebreaks

### Clone Actor

Click on the "Clone Actor" tab and seach up for the Actor u want to Clone

U can also Clone Actors which u have already created, via the top right button "Orignial" and change it to Mod

Beneath that u can put the name of your Duped Actor, it SHOULD check if the name already is used in the mod, but still try to remember which actornames youve used

*known bug: some actors modelrenaming may be weird (e.g in the modelinfo for koopa, it changes the name from the koopa model and from its shell model)

U can hover over the checkboxes and see a tooltip which explains what it does.

-"Copy Pack" copies the .pack.zs itself

-"Copy BFRES" copies the Model file

-"Rename BFRES internals" renames the internal names inside the BFRES, not just the filename

-"Copy Animation" also copies the animation BFRES

-"Adjust ActorEngine" renames the ActorEngine file inside the pack file.

-"Adjust ModelInfo Refernces" copies the BFRES.zs file, renames the BFRES and the Model to the Name which is set in the "New Actor Name" , renames the modelinfo inside the engine file and inside the Component\Modelinfo sets the RSDB and ModelProjectName to the value set in the "New Actor Name"

-"Adjust RSDB Entries" Adds the Entry into both Tables, if they are set in the Settings (it copies the Entries from the OG one and change the Fmdb, ModelProjectName and the Rowid)

-"Adjust RSTBL" not implemented

Click "Clone Actor" if u Think u got everysetting u want

If u have the RSTB.exe defined in the settings, u can run it here.

### Import Actor (Experimental)

Here u can import Actors via their Files.
  -Pack file: chose the pack file
  -BFRES file: chose the bfres file (if there is one)
    -U can also use a different model (e.g a selfmade one) and check the "adjust modelinfo references" so it can update the modelinfo
  -Animation file: chose the animation bfres file (if there is one)
  -Actor Name: if u want to rename the actor u want to import, rename it here and click the "Adjust ActorEngine"
  -ActorInfo/GameActorInfo: here u can do following things:
    -Do not add: doesnt add the actor to the ActorInfo/GameActorInfo
    -Copy from existing Actors: here u can choose to copy it from an existing actor
    -Own text: here u can copy the Actorinfo/GameActorInfo. Needs to be Json.


### Actor Wizard
Here i tried to make some changes more streamlined

On the left u choose which actor u want to edit

Then u can chose which part u want to edit (Physics (not fully tested), Damage Reaction, Hitboxes, Attack Info)

Physics: 
  -Its more to look at some values and see what kind of attribute it has, right now u can look at GamePhysicsParam, ThrowParam, SpeedSetParam.
  -Editing it here is not endored, cause i just dont edit this kind of stuff here so i havnt really tested it and havent really looked into it, what kind of stuff it does and breaks

Damage Reaction:
  -Here u can edit the Damage Reaction of the Actor via a Dropdown list.
  -If u edit the damage reaction of an cloned actor, u need to create a new DamageReactionParam file via the "Create own DamageReaction Table" button, so u dont overwrite the original one of the original actor
  -Then u see a list with damage Types, Source (if its from its own Table or its Parent), ReactionType, Deathtype and NumToDie
    -Damage Types: the type of damage (Poison, Water, Stamp etc)
    -Source: if its from the original actor or from the cloned one, if its an inherited one, u can use the dropdownbox beneath to choose which one u want to inherit and edit it
    -ReactionType: what kind of reaction the actor has to this damage (Bounce, Stop, Fall, etc)
    -Deathtype: what kind of deathtype the actor has (Eaten, DieStamp, SchockWave, etc)
    -NumToDie: how many times the actor can take this damage before it dies (1, 2, 3 etc)
      -(tbh i dont know the difference between 0 and "(not set)", cause both of them cause to let the actor not die from this damagetype)
  If ur done editing, click the "Apply to Pack" and then the Save Pack button

Hitboxes:
  -Here u can edit Hitboxes of an Actor
  -Here u have the following Tabs: Manage Hitbxes, Visual Editor, Hitbox Behavior
  -Manage Hitboxes:
    -Here u can see the exising Hitboxes of the Actor, create new one from one of the existing ones, delete them.
  -Visual Editor:
    -Here u can see the Hitboxes on a 2d Plane, where u can change the Shape of the Hitbox (only sphere, box Capsule) and rezite and move them
  -Hitbox Behavior:
    -Only edit if u know what ur doing, cause i cant explain fully what each value does
    -LayerSensor defines (i think) which type of trigger it has, e.g EnemyAttack only triggers on Players, not on other Actors like Enemies. The other values i havnt really looked into so i cant explain them

Attack Info:
  -Here u can edit the AtType of an Sensor. E.g Fire, Electricity, Reflect etc.
  -Some AtType also have an Attribute, like Knochback, wher u can set if its an small, medium or big knockback
  -SensorTagName defines, which Sensor name it is


### Pack Editor
 
Here u can edit (i think) almost every kind of file. U can select the Source of the Actors, so can see the Romfs and ur Modded Romfs Actors. U can also Open other files with the "Open" button. On the left u can see the Actors which u can open. Once opened, on the top u can see each Actor as its own Tab, so u can quickly switch between them if u want. Once opened, u see the folders and files inside the Actor. If u want to search smth specific, u can use the searchbar and activate the "search content" function, which also searches inside the files. Files which are white have the same name as the content of the searchbar, files which are orange have contents which match the searchbar content. U can edit specific files with a double click. When u change smth, the "Save File" button becomes Green (sometimes the color doenst change) and if u click on it you temperarily save the file, but it wont be saved to the pack until u click the "Save" button on the top left. Inside the file u want to edit, u can also export and import the contents as a json (or u can just ctrl+ c and ctrl+v the contents into the json editor). U can edit smaller files inside this tool, but i would recommend to change bigger files in e.g Vs-Code, cause the editor is not optimal for searches inside the file. U can right click a file to do varois things, like duplicate, rename, delete, export and rename it cascadingly (renames the file and all references).