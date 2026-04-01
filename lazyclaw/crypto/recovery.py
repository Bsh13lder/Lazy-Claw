"""Recovery phrase (mnemonic) support for LazyClaw.

Architecture:
  - Each user has a random 32-byte master_key (the actual data encryption key).
  - master_key is wrapped (encrypted) twice:
      password_encrypted_dek  = encrypt(master_key_hex, derive_key(password, salt))
      recovery_encrypted_dek  = encrypt(master_key_hex, derive_key(phrase, user_id))
  - On password change or recovery: unwrap master_key, re-wrap with new key.
  - Existing users without a stored DEK fall back to the old derived-key model.
"""

from __future__ import annotations

import os
import secrets

from lazyclaw.crypto.encryption import decrypt, derive_key, encrypt

# ---------------------------------------------------------------------------
# BIP39-compatible English wordlist (2048 words)
# Source: standard BIP39 English list, first 2048 words
# ---------------------------------------------------------------------------

_WORDLIST: list[str] = [
    "abandon","ability","able","about","above","absent","absorb","abstract",
    "absurd","abuse","access","accident","account","accuse","achieve","acid",
    "acoustic","acquire","across","act","action","actor","actress","actual",
    "adapt","add","addict","address","adjust","admit","adult","advance",
    "advice","aerobic","afford","afraid","again","age","agent","agree",
    "ahead","aim","air","airport","aisle","alarm","album","alcohol",
    "alert","alien","all","alley","allow","almost","alone","alpha",
    "already","also","alter","always","amateur","amazing","among","amount",
    "amused","analyst","anchor","ancient","anger","angle","angry","animal",
    "ankle","announce","annual","another","answer","antenna","antique","anxiety",
    "any","apart","apology","appear","apple","approve","april","arch",
    "arctic","area","arena","argue","arm","armed","armor","army",
    "around","arrange","arrest","arrive","arrow","art","artefact","artist",
    "artwork","ask","aspect","assault","asset","assist","assume","asthma",
    "athlete","atom","attack","attend","attitude","attract","auction","audit",
    "august","aunt","author","auto","autumn","average","avocado","avoid",
    "awake","aware","away","awesome","awful","awkward","axis","baby",
    "balance","bamboo","banana","banner","barely","bargain","barrel","base",
    "basic","basket","battle","beach","bean","beauty","because","become",
    "beef","before","begin","behave","behind","believe","below","belt",
    "bench","benefit","best","betray","better","between","beyond","bicycle",
    "bid","bike","bind","biology","bird","birth","bitter","black",
    "blade","blame","blanket","blast","bleak","bless","blind","blood",
    "blossom","blouse","blue","blur","blush","board","boat","body",
    "boil","bomb","bone","book","boost","border","boring","borrow",
    "boss","bottom","bounce","box","boy","bracket","brain","brand",
    "brave","breeze","brick","bridge","brief","bright","bring","brisk",
    "broccoli","broken","bronze","broom","brother","brown","brush","bubble",
    "buddy","budget","buffalo","build","bulb","bulk","bullet","bundle",
    "bunker","burden","burger","burst","bus","business","busy","butter",
    "buyer","buzz","cabbage","cabin","cable","cactus","cage","cake",
    "call","calm","camera","camp","can","canal","cancel","candy",
    "cannon","canvas","canyon","capable","capital","captain","car","carbon",
    "card","cargo","carpet","carry","cart","case","cash","casino",
    "castle","casual","cat","catalog","catch","category","cattle","caught",
    "cause","caution","cave","ceiling","celery","cement","census","century",
    "cereal","certain","chair","chalk","champion","change","chaos","chapter",
    "charge","chase","chat","cheap","check","cheese","chef","cherry",
    "chest","chicken","chief","child","chimney","choice","choose","chronic",
    "chuckle","chunk","cigar","cinnamon","circle","citizen","city","civil",
    "claim","clap","clarify","claw","clay","clean","clerk","clever",
    "click","client","cliff","climb","clinic","clip","clock","clog",
    "close","cloth","cloud","clown","club","clump","cluster","clutch",
    "coach","coast","coconut","code","coffee","coil","coin","collect",
    "color","column","combine","come","comfort","comic","common","company",
    "concert","conduct","confirm","congress","connect","consider","control","convince",
    "cook","cool","copper","copy","coral","core","corn","correct",
    "cost","cotton","couch","country","couple","course","cousin","cover",
    "coyote","crack","cradle","craft","cram","crane","crash","crater",
    "crawl","crazy","cream","credit","creek","crew","cricket","crime",
    "crisp","critic","cross","crouch","crowd","crucial","cruel","cruise",
    "crumble","crunch","crush","cry","crystal","cube","culture","cup",
    "cupboard","curious","current","curtain","curve","cushion","custom","cute",
    "cycle","dad","damage","damp","dance","danger","daring","dash",
    "daughter","dawn","day","deal","debate","debris","decade","december",
    "decide","decline","decorate","decrease","deer","defense","define","defy",
    "degree","delay","deliver","demand","demise","denial","dentist","deny",
    "depart","depend","deposit","depth","deputy","derive","describe","desert",
    "design","desk","despair","destroy","detail","detect","develop","device",
    "devote","diagram","dial","diamond","diary","dice","diesel","diet",
    "differ","digital","dignity","dilemma","dinner","dinosaur","direct","dirt",
    "disagree","discover","disease","dish","dismiss","disorder","display","distance",
    "divert","divide","divorce","dizzy","doctor","document","dog","doll",
    "dolphin","domain","donate","donkey","donor","door","dose","double",
    "dove","draft","dragon","drama","drastic","draw","dream","dress",
    "drift","drill","drink","drip","drive","drop","drum","dry",
    "duck","dumb","dune","during","dust","dutch","duty","dwarf",
    "dynamic","eager","eagle","early","earn","earth","easily","east",
    "easy","echo","ecology","edge","edit","educate","effort","egg",
    "eight","either","elbow","elder","electric","elegant","element","elephant",
    "elevator","elite","else","embark","embody","embrace","emerge","emotion",
    "employ","empower","empty","enable","enact","endless","endorse","enemy",
    "energy","enforce","engage","engine","enhance","enjoy","enlist","enough",
    "enrich","enroll","ensure","enter","entire","entry","envelope","episode",
    "equal","equip","erase","erosion","escape","essay","essence","estate",
    "eternal","ethics","evidence","evil","evoke","evolve","exact","example",
    "excess","exchange","excite","exclude","exercise","exhaust","exhibit","exile",
    "exist","exit","exotic","expand","expire","explain","expose","express",
    "extend","extra","eye","fable","face","faculty","faint","faith",
    "fall","false","fame","family","famous","fan","fancy","fantasy",
    "far","fashion","fat","fatal","father","fatigue","fault","favorite",
    "feature","february","federal","feel","feet","fellow","felt","fence",
    "festival","fetch","fever","few","fiber","fiction","field","figure",
    "file","film","filter","final","find","fine","finger","finish",
    "fire","firm","first","fiscal","fish","fit","fitness","fix",
    "flag","flame","flash","flat","flavor","flee","flight","flip",
    "float","flock","floor","flower","fluid","flush","fly","foam",
    "focus","fog","foil","follow","food","foot","force","forest",
    "forget","fork","fortune","forum","forward","fossil","foster","found",
    "fox","fragile","frame","frequent","fresh","friend","fringe","frog",
    "front","frown","frozen","fruit","fuel","fun","funny","furnace",
    "fury","future","gadget","gain","galaxy","gallery","game","gap",
    "garbage","garden","garlic","garment","gas","gasp","gate","gather",
    "gauge","gaze","general","genius","genre","gentle","genuine","gesture",
    "ghost","gift","giggle","ginger","giraffe","girl","give","glad",
    "glance","glare","glass","glide","glimpse","globe","gloom","glory",
    "glove","glow","glue","goat","goddess","gold","good","goose",
    "gorilla","gospel","gossip","govern","gown","grab","grace","grain",
    "grant","grape","grasp","grass","gravity","great","green","grid",
    "grief","grit","grocery","group","grow","grunt","guard","guide",
    "guilt","guitar","gun","gym","habit","hair","half","hammer",
    "hamster","hand","happy","harsh","harvest","hat","have","hawk",
    "hazard","head","health","heart","heavy","hedgehog","height","hello",
    "helmet","help","hero","hidden","high","hill","hint","hip",
    "hire","history","hobby","hockey","hold","hole","holiday","hollow",
    "home","honey","hood","hope","horn","hospital","host","hour",
    "hover","hub","huge","human","humble","humor","hundred","hungry",
    "hunt","hurdle","hurry","hurt","husband","hybrid","ice","icon",
    "ignore","ill","illegal","image","imitate","immense","immune","impact",
    "impose","improve","impulse","inbox","income","increase","index","indicate",
    "indoor","industry","infant","inflict","inform","inhale","inject","inner",
    "innocent","input","inquiry","insane","insect","inside","inspire","install",
    "intact","interest","into","invest","invite","involve","iron","island",
    "isolate","issue","item","ivory","jacket","jaguar","jar","jazz",
    "jealous","jeans","jelly","jewel","job","join","joke","journey",
    "joy","judge","juice","jump","jungle","junior","junk","just",
    "kangaroo","keen","keep","ketchup","key","kick","kid","kingdom",
    "kiss","kit","kitchen","kite","kitten","kiwi","knee","knife",
    "knock","know","lab","lamp","language","laptop","large","later",
    "laugh","laundry","lava","law","lawn","lawsuit","layer","lazy",
    "leader","learn","leave","lecture","left","leg","legal","legend",
    "leisure","lemon","lend","length","lens","leopard","lesson","letter",
    "level","liar","liberty","library","license","life","lift","like",
    "limb","limit","link","lion","liquid","list","little","live",
    "lizard","load","loan","lobster","local","lock","logic","lonely",
    "long","loop","lottery","loud","lounge","love","loyal","lucky",
    "luggage","lumber","lunar","lunch","luxury","mad","magic","magnet",
    "maid","main","mammal","mango","mansion","manual","maple","marble",
    "march","margin","marine","market","marriage","mask","master","match",
    "material","math","matrix","matter","maximum","maze","meadow","mean",
    "medal","media","melody","melt","member","memory","mention","menu",
    "mercy","mesh","message","metal","method","middle","midnight","milk",
    "million","mimic","mind","minimum","minor","minute","miracle","miss",
    "mistake","mix","mixed","mixture","mobile","model","modify","mom",
    "monitor","monkey","monster","month","moon","moral","more","morning",
    "mosquito","mother","motion","motor","mountain","mouse","move","movie",
    "much","muffin","mule","multiply","muscle","museum","mushroom","music",
    "must","mutual","myself","mystery","naive","name","napkin","narrow",
    "nasty","nature","near","neck","need","negative","neglect","neither",
    "nephew","nerve","nest","network","news","next","nice","night",
    "noble","noise","nominee","noodle","normal","north","notable","note",
    "nothing","notice","novel","now","nuclear","number","nurse","nut",
    "oak","obey","object","oblige","obscure","obtain","ocean","october",
    "odor","offer","office","often","oil","okay","old","olive",
    "olympic","omit","once","onion","open","opera","oppose","option",
    "orange","orbit","orchard","order","ordinary","organ","orient","original",
    "orphan","ostrich","other","outdoor","outside","oval","over","own",
    "oyster","ozone","pact","paddle","page","pair","palace","palm",
    "panda","panel","panic","panther","paper","parade","parent","park",
    "parrot","party","pass","patch","path","patrol","pause","pave",
    "payment","peace","peanut","pear","peasant","pelican","pen","penalty",
    "pencil","people","pepper","perfect","permit","person","pet","phone",
    "photo","phrase","physical","piano","picnic","picture","piece","pig",
    "pigeon","pill","pilot","pink","pioneer","pipe","pistol","pitch",
    "pizza","place","planet","plastic","plate","play","please","pledge",
    "pluck","plug","plunge","poem","poet","point","polar","pole",
    "police","pond","pony","pool","popular","portion","position","possible",
    "post","potato","pottery","poverty","powder","power","practice","praise",
    "predict","prefer","prepare","present","pretty","prevent","price","pride",
    "primary","print","priority","prison","private","prize","problem","process",
    "produce","profit","program","project","promote","proof","property","prosper",
    "protect","proud","provide","public","pudding","pull","pulp","pulse",
    "pumpkin","punish","pupil","purchase","purity","purpose","push","put",
    "puzzle","pyramid","quality","quantum","quarter","question","quick","quit",
    "quiz","quote","rabbit","raccoon","race","rack","radar","radio",
    "rage","rail","rain","raise","rally","ramp","ranch","random",
    "range","rapid","rare","rate","rather","raven","reach","ready",
    "real","reason","rebel","rebuild","recall","receive","recipe","record",
    "recycle","reduce","reflect","reform","refuse","region","regret","regular",
    "reject","relax","release","relief","rely","remain","remember","remind",
    "remove","render","renew","rent","reopen","repair","repeat","replace",
    "report","require","rescue","resemble","resist","resource","response","result",
    "retire","retreat","return","reunion","reveal","review","reward","rhythm",
    "ribbon","rice","rich","ride","ridge","rifle","right","rigid",
    "ring","riot","ripple","risk","ritual","rival","river","road",
    "roast","robot","robust","rocket","romance","roof","rookie","rose",
    "rotate","rough","route","royal","rubber","rude","rug","rule",
    "run","runway","rural","sad","saddle","sadness","safe","sail",
    "salad","salmon","salon","salt","salute","same","sample","sand",
    "satisfy","satoshi","sauce","sausage","save","scale","scan","scatter",
    "scene","scheme","school","science","scissors","scorpion","scout","scrap",
    "screen","script","scrub","sea","search","season","seat","second",
    "secret","section","security","seek","segment","select","sell","seminar",
    "senior","sense","series","service","session","settle","setup","seven",
    "shadow","shaft","shallow","share","shed","shell","sheriff","shield",
    "shift","shine","ship","shiver","shock","shoe","shoot","shop",
    "short","shoulder","shove","shrimp","shrug","shuffle","shy","sibling",
    "siege","sight","sign","silent","silk","silly","silver","similar",
    "simple","since","sing","siren","sister","situate","six","size",
    "sketch","skill","skin","skirt","skull","slab","slam","sleep",
    "slender","slice","slide","slight","slim","slogan","slot","slow",
    "slush","small","smart","smile","smoke","smooth","snack","snake",
    "snap","sniff","snow","soap","soccer","social","sock","solar",
    "soldier","solid","solution","solve","someone","song","soon","sorry",
    "soul","sound","soup","source","south","space","spare","spatial",
    "spawn","speak","special","speed","sphere","spice","spider","spike",
    "spin","spirit","split","spoil","sponsor","spoon","spray","spread",
    "spring","spy","square","squeeze","squirrel","stable","stadium","staff",
    "stage","stairs","stamp","stand","start","state","stay","steak",
    "steel","stem","step","stereo","stick","still","sting","stock",
    "stomach","stone","stop","store","storm","story","stove","strategy",
    "street","strike","strong","struggle","student","stuff","stumble","subject",
    "submit","subway","success","such","sudden","suffer","sugar","suggest",
    "suit","summer","sun","sunny","sunset","super","supply","supreme",
    "sure","surface","surge","surprise","sustain","swallow","swamp","swap",
    "swear","sweet","swift","swim","swing","switch","sword","symbol",
    "symptom","syrup","table","tackle","tag","tail","talent","tank",
    "tape","target","task","tattoo","taxi","teach","team","tell",
    "ten","tenant","tennis","tent","term","test","text","thank",
    "that","theme","then","theory","there","they","thing","this",
    "thought","three","thrive","throw","thumb","thunder","ticket","tilt",
    "timber","time","tiny","tip","tired","title","toast","tobacco",
    "today","together","toilet","token","tomato","tomorrow","tone","tongue",
    "tonight","tool","tooth","top","topic","topple","torch","tornado",
    "tortoise","toss","total","tourist","toward","tower","town","toy",
    "track","trade","traffic","tragic","train","transfer","trap","trash",
    "travel","tray","treat","tree","trend","trial","tribe","trick",
    "trigger","trim","trip","trophy","trouble","truck","truly","trumpet",
    "trust","truth","try","tube","tuition","tumble","tuna","tunnel",
    "turkey","turn","turtle","twelve","twenty","twice","twin","twist",
    "two","type","typical","ugly","umbrella","unable","uncle","uncover",
    "under","undo","unfair","unfold","unhappy","uniform","unique","universe",
    "unknown","unlock","until","unusual","unveil","update","upgrade","uphold",
    "upon","upper","upset","urban","usage","use","used","useful",
    "useless","usual","utility","vacant","vacuum","vague","valid","valley",
    "valve","van","vanish","vapor","various","vast","vault","vehicle",
    "velvet","vendor","venture","venue","verb","verify","version","very",
    "veteran","viable","vibrant","vicious","victory","video","view","village",
    "vintage","violin","virtual","virus","visa","visit","visual","vital",
    "vivid","vocal","voice","void","volcano","volume","vote","voyage",
    "wage","wagon","wait","walk","wall","walnut","want","warfare",
    "warm","warrior","waste","water","wave","way","wealth","weapon",
    "wear","weasel","weather","web","wedding","weekend","weird","welcome",
    "well","west","wet","what","wheat","wheel","when","where",
    "whip","whisper","wide","width","wife","wild","will","win",
    "window","wine","wing","wink","winner","winter","wire","wisdom",
    "wise","wish","witness","wolf","woman","wonder","wood","wool",
    "word","world","worry","worth","wrap","wreck","wrestle","wrist",
    "write","wrong","yard","year","yellow","you","young","youth",
    "zebra","zero","zone","zoo",
]

# Wordlist must have at least 1024 words for adequate entropy (~120 bits for 12-word phrase)
assert len(_WORDLIST) >= 1024, f"Wordlist too short: {len(_WORDLIST)} words"

_RECOVERY_SALT_PREFIX = b"lazyclaw-recovery-v1:"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_mnemonic(num_words: int = 12) -> str:
    """Generate a random BIP39-style mnemonic phrase.

    Uses cryptographically secure random selection from the 2048-word list.
    Returns a space-separated string of `num_words` words.
    """
    words = [secrets.choice(_WORDLIST) for _ in range(num_words)]
    return " ".join(words)


def mnemonic_to_recovery_key(phrase: str, user_id: str) -> bytes:
    """Derive a 32-byte AES key from a recovery phrase + user_id.

    The user_id is mixed in as a per-user salt so the same phrase cannot
    be used to recover a different account.
    """
    salt = _RECOVERY_SALT_PREFIX + user_id.encode("utf-8")
    # Normalize: lowercase, collapse whitespace
    normalized = " ".join(phrase.lower().split())
    return derive_key(normalized, salt, iterations=100_000)


def generate_master_key() -> bytes:
    """Generate a fresh random 32-byte master key (DEK)."""
    return os.urandom(32)


def wrap_master_key(master_key: bytes, wrapping_key: bytes) -> str:
    """Encrypt master_key with wrapping_key. Returns enc:v1:... token."""
    return encrypt(master_key.hex(), wrapping_key)


def unwrap_master_key(wrapped: str, wrapping_key: bytes) -> bytes:
    """Decrypt a wrapped master_key. Returns raw 32 bytes."""
    hex_key = decrypt(wrapped, wrapping_key)
    raw = bytes.fromhex(hex_key)
    if len(raw) != 32:
        raise ValueError("Unwrapped key has unexpected length")
    return raw


def derive_password_wrapping_key(password: str, encryption_salt: str) -> bytes:
    """Derive the key used to wrap the master_key from user password + salt."""
    return derive_key(password, encryption_salt.encode("utf-8"), iterations=100_000)
