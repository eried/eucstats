"""Public-site localization.

`EN` is the canonical English source — the single source of truth and the
fallback for any missing key. `TRANSLATIONS` holds the other 18 locales
supported by eucplanet (da, de, es, es-419, fr, it, ja, ko, nl, no, pl,
pt-BR, ru, sv, tr, uk, zh, zh-Hant). `langs_payload()` returns
{locale: {key: text}} for injection into the public page as
window.__I18N__; the client picks a locale (browser
auto-detect or the saved cogwheel choice) and falls back to EN per key.

Admin is intentionally NOT localized. The big red banner stays in its
original wording (it's set by the admin).
"""
from __future__ import annotations

# --- canonical English source (key -> text) -------------------------------
# Keys are grouped by area. {x} placeholders are filled on the client and must
# be preserved verbatim by every translation.
EN: dict[str, str] = {
    # dock (bottom bar) — short labels, keep tight
    "dock.riders": "Riders",
    "dock.crews": "Crews",
    "dock.countries": "Countries",
    "dock.wheels": "Wheels",
    "dock.brands": "Brands",
    "dock.records": "Records",
    "dock.app": "App",
    # crews & territory. Kept short and plain on purpose: this is a game mode, not a manual.
    "crew.board": "Who owns the streets",
    "crew.tiles": "{n} squares",
    "crew.tiles.few": "{n} squares",
    "crew.tile1": "{n} square",
    "crew.patches.few": "{n} patches",
    "crew.patches": "{n} patches",
    "crew.inall": "{v} all told",
    # signing in
    "crew.targets.grows": "your block goes to {n}",
    "crew.targets.youpassname": "takes you past {name}",
    "crew.targets.clear": "Nobody else is riding near you. Each of these adds one square to your block.",
    "crew.targets.stray": "adds a square, but not to your block",
    "crew.join.pending.none": "Nobody here can let you in. This crew has no leader.",
    # The panel's own chrome. `pclose` closes the whole feature and announced itself
    # as "button"; `ppeek` is admin-only and was the one hardcoded English tooltip
    # beside a sibling that already used data-i18n-title.
    # the anonymous-country globe, which a screen reader reads aloud
    "flag.hidden": "Country hidden",
    # The page a scanned QR lands on, served from crews_api by Accept-Language. It
    # was hard-coded English in all nineteen, including the notice that says what a
    # pass can and cannot do -- which is the one string on it that matters.
    "pair.h": "Crew Pass",
    "pair.p": "Say yes in {app} and that browser can fly your colours.",
    "pair.noapp": "App did not open? Start it yourself, go to {crews}, and punch in the code above.",
    "pair.safe": "Only say yes to a code you asked for. A pass lets a browser act for you in crews: start one, join one, leave one. It can't send up rides, rename you, or delete anything.",
    # A code lives three minutes and works once, so a scan of a photographed QR is likelier
    # to be stale than live -- and this page used to render identically either way.
    "pair.dead.h": "That code is done",
    "pair.dead.p": "A code lasts three minutes and works once. Open {crews} on the site and it will hand you a fresh one.",
    "pair.site": "Open EUC Stats",
    "panel.close": "Close",
    "panel.peek": "Preview as a normal visitor",
    "crew.wip": "Not open for public testing yet. Anything you build here can be wiped without warning.",
    "crew.signin.h": "Get started",
    "crew.signin.p": "Your app vouches for you once. Then you can start a crew and go take ground.",
    # The line above the code, picked at random per visit. Three reviewers independently said
    # the signed-out screen never says what this IS or why you would want it -- it was a QR
    # under the word "Get started" and nothing else, and the pitch that existed was rendered
    # nowhere. These are the hook; `crew.pub.what` under them is the mechanic in one sentence.
    "crew.hook.1": "Take over the world. The EUC bit of it, anyway.",
    "crew.hook.2": "The streets are going to belong to somebody. Might as well be you.",
    "crew.hook.3": "Ride it and it is yours. Stop, and somebody comes for it.",
    "crew.hook.4": "Your city, in your colours, if you can hold it.",
    "crew.hook.5": "Every square out there has somebody's name on it. Change that.",
    "crew.hook.6": "Turn the ride home into a land grab.",
    "crew.signin.scan": "Scan it with EUC Planet {v} or newer, or type the code in.",
    # Crews pairing lands in EUC Planet 0.22.0. The number is a variable and comes
    # from services.pairing.MIN_APP, so a release is one line and not nineteen.
    "crew.signin.needs": "Needs EUC Planet {v} or newer.",
    # Said BEFORE the scan, not after. A rider with no uploaded ride is refused server-side at
    # `/pair/confirm` with "That rider is not registered." -- a clear sentence that goes to the
    # phone, not to this card, so the browser went on showing a code that could never work
    # until it expired. The precondition belongs where the code is offered.
    "crew.signin.ride": "Upload a ride first. Crews are for riders the site already knows.",
    # the QR image's only name: the first card a new rider sees
    "crew.signin.qralt": "Crew pass code",
    # One caption under the code, because the code IS the link. This was a sentence saying you
    # cannot scan your own screen followed by a button going where the code goes -- the same
    # control twice, with an apology between them.
    "crew.signin.tap": "On this phone? Tap the code to open EUC Planet.",
    # starting one
    "crew.new.h": "Start a crew",
    "crew.new.p": "We picked colours nobody else is flying. Change them if you like.",
    "crew.new.name": "Name",
    "crew.new.desc": "Description",
    "crew.new.colours": "Colours",
    # `solid`, `stripes`, `dots` and `hatch` were raw array identifiers, and the colour cells
    # were named by hex code -- twenty-four of them read aloud, and the only strings in the
    # create card that did not change with the language.
    "crew.new.patterns": "Patterns",
    "crew.pattern.solid": "solid",
    "crew.pattern.stripes": "stripes",
    "crew.pattern.dots": "dots",
    "crew.pattern.hatch": "hatch",
    "crew.pattern.backslash": "back stripes",
    "crew.pattern.vert": "vertical",
    "crew.pattern.horiz": "horizontal",
    "crew.pattern.grid": "grid",
    "crew.pattern.bigdots": "spots",
    "crew.pattern.rings": "rings",
    "crew.pattern.checker": "checks",
    "crew.pattern.bricks": "bricks",
    # The swatch's own name plus its index. Forty-eight of these said "Colour 1".."Colour 48"
    # while the patterns beside them said "stripes" -- in the grid where somebody who cannot
    # see the difference needs words most. The name is derived from the hex; see colourName.
    "crew.new.colourn": "{v}, colour {n}",
    "crew.hue.red": "red",
    "crew.hue.orange": "orange",
    "crew.hue.brown": "brown",
    "crew.hue.yellow": "yellow",
    "crew.hue.lime": "lime",
    "crew.hue.green": "green",
    "crew.hue.teal": "teal",
    "crew.hue.blue": "blue",
    "crew.hue.purple": "purple",
    "crew.hue.pink": "pink",
    "crew.hue.grey": "grey",
    "crew.hue.white": "white",
    "crew.hue.black": "black",
    "crew.hue.pale": "pale {v}",
    "crew.hue.dark": "dark {v}",
    # What a greyed swatch means, appended to its own name so the label reads
    # "stripes — another crew flies this" rather than needing a legend of its own.
    "crew.new.gone": "another crew flies this",
    # Both grids draw a taken chip drained and struck through, which reads as unavailable to
    # anybody who knows the convention and to nobody else. `title` carried the reason and a
    # phone has no hover, so a touch reader tapped a greyed square and got nothing at all.
    # Shown only when something in the grid actually is taken.
    "crew.new.dimmed": "Crossed out means another crew already flies it.",
    "crew.new.who": "Who can join",
    "crew.new.approval": "A leader says yes",
    "crew.new.open": "Anyone",
    "crew.new.invite": "Invite code only",
    "crew.new.go": "Create crew",
    # joining
    "crew.join.h": "Join a crew",
    # Sixteen crews is about 1,800px with no way to narrow it, and once joining
    # comes before founding that 1,800px is also what stands in front of the
    # create form. Six nearest, a box, and the rest behind the toggle.
    "crew.join.filter": "Find a crew",
    # The list changed under a search box and nothing was announced, not even that
    # there were no results. A live status beside the box carries the count.
    "crew.join.count": "{n} of {v} crews",
    "crew.join.all": "Show all {n}",
    "crew.join.fewer": "Show fewer",
    "crew.join.btn": "Join",
    "crew.join.ask": "Ask",
    "crew.join.code": "Enter code",
    "crew.join.codeask": "{name}'s invite code?",
    # Joining costs a cooldown on the way back out, and took one tap with nothing said,
    # while pulling a request -- which costs nothing -- opened a dialog.
    "crew.join.confirm": "Ride for {name}? If you leave again you wait {v} before joining another.",
    # Asking is not joining: nothing is given up until a leader says yes, and the
    # withdrawal dialog two taps later already says "Costs you nothing."
    "crew.join.askconfirm": "Ask to ride for {name}? Nothing changes until a leader says yes.",
    "crew.cancel": "Cancel",
    "crew.join.pending": "Waiting on a leader to let you in.",
    "crew.join.wait.h": "Cooling off",
    "crew.join.wait.p": "You just walked out of one. Next crew in {n}. Keeps people from hopping around farming ground.",
    "crew.first.building": "The map is still being drawn. Ground shows up within the hour.",
    "crew.first.h": "Ride something first",
    "crew.first.p": "Crews are for riders. Send up one ride and you can start your own. Joining one works right now.",
    # your crew
    "crew.mine.ao": "in one piece",
    "crew.mine.start": "Ride a {n}x{n} block and you're on the map",
    "crew.mine.who": "Who rode for it",
    # The marker on your own row. Not `crew.how.s3` ("Your crew"): these rows are riders,
    # and a row that is YOU labelled "Your crew" is the wrong noun in the one list that is
    # explicitly about people. The i18n guard caught the reuse and it was right to.
    "crew.you": "You",
    "crew.targets.h": "Where to ride next",
    "crew.targets.p": "How much more you have to ride inside each one. Pick one to find it.",
    "crew.targets.first": "part of your first block",
    "crew.take.1": "one lap",
    "crew.take.2": "a few streets",
    "crew.take.3": "a short ride",
    "crew.lose.nowwho": "{name} is taking it",
    "crew.lose.soonwho": "{name} is creeping up",
    "crew.take.4": "a proper ride",
    "crew.take.5": "a long ride",
    "crew.take.6": "a day out",
    "crew.hold.1": "a street or two clear",
    "crew.hold.2": "a good way clear",
    "crew.hold.3": "nobody within sight",
    "crew.tile.needw": "{v} and it flips",
    "crew.tile.fresh": "new this week",
    "crew.targets.kills": "breaks their block, {v} go",
    "crew.targets.joins": "joins two patches",
    "crew.targets.blocked": "ridden out, take the one next door",
    "crew.targets.taken": "{name} has it",
    "crew.targets.none": "Nothing to aim at yet. Ride a {n}x{n} block anywhere and this fills up.",
    "crew.targets.n": "N",
    "crew.targets.ne": "NE",
    "crew.targets.e": "E",
    "crew.targets.se": "SE",
    "crew.targets.s": "S",
    "crew.targets.sw": "SW",
    "crew.targets.w": "W",
    "crew.targets.nw": "NW",
    "crew.lose.h": "What you could lose",
    "crew.lose.p": "They are this close to taking it. Get back over them.",
    "crew.mine.cancel": "Pull the request",
    "crew.mine.cancelq": "Pull your request to {name}? Costs you nothing.",
    "crew.mine.disband": "Disband",
    "crew.mine.disbandq": "Disband {name}? Colours go back in the box and the map fades out on its own. No waiting afterwards — you can start or join another straight away.",
    # The same question when other people are in it. The single-string version was read
    # out word for word to the leader of a five-rider crew sitting first on the board,
    # and it never mentioned that four other riders lose their crew. `leaveq` has had
    # four variants for less than this. Phrased so one rider and four read the same way:
    # "takes it away from 1 rider besides you" needs no verb agreement.
    "crew.mine.disbandq.others": "Disband {name}? That takes it away from {r} besides you, and they get no say in it. Colours go back in the box and the map fades out on its own. No waiting afterwards — you can start or join another straight away.",
    "crew.mine.onmap": "Show your crew's ground on the map",
    "crew.asked.ago": "asked {d} ago",
    # The short end, which `crew.ago.` has no buckets for: its floor is "a day or two",
    # and a knock that arrived two minutes ago reading "asked a day or two ago" is wrong
    # in the one place a leader makes a decision. The hour form carries the figure rather
    # than spelling it, so this is two strings and not a plural set in nineteen tables.
    "crew.asked.now": "asked just now",
    "crew.asked.h": "asked {n}h ago",
    "crew.mine.claim": "Take over",
    "crew.mine.claimq.none": "Nobody is running this crew. Take it over?",
    "crew.mine.claimq": "Your leader has gone quiet. Take the crew over?",
    # The shape of the threat, above the squares it is made of. Three reviewers all said the
    # same thing about this card: thirty-odd near-identical rows stop reading as urgency. `{v}`
    # arrives already pluralised from `tiles()`, like `crew.board.gained`, so the noun
    # agreement is not nineteen translators' problem.
    "crew.lose.threat": "{name} is closing in on {v}.",
    # The rest of the card's total, which the rival lines cannot account for: ground nobody is
    # taking, that the crew has simply stopped riding. Without it the three threat numbers sat
    # under a headline seven squares larger with nothing nearby to explain the gap. Its own
    # string rather than `tiles()` plus the per-row `crew.lose.cold` fragment -- a counted
    # sentence assembled out of another string's words is how `crew.how.7` shipped broken
    # grammar to German and Russian. `{v}` arrives pluralised, like its sibling above.
    "crew.lose.cold.n": "{v} gone cold. Anyone could take them with one ride.",
    "crew.lose.gap": "{v} and it's theirs",
    "crew.lose.now": "this week",
    "crew.lose.soon": "creeping up",
    "crew.lose.cold": "cold, and cheap for anyone",
    "crew.lose.more": "and {v} more",
    "crew.tile.since": "held for {d}",
    "crew.ago.new": "a day or two",
    "crew.ago.days": "{n} days",
    "crew.ago.days.few": "{n} days",
    "crew.ago.weeks": "{n} weeks",
    "crew.ago.weeks.few": "{n} weeks",
    "crew.ago.year": "over a year",
    "crew.board.gained": "+{v} this week",
    "crew.legend.note": "The breathing is your own ground. Everyone else's sits still.",
    # The same distinction for a reader who asked for stillness. The old sentence told them
    # to look for a cue that, for them, is not drawn at all.
    "crew.legend.note.calm": "What is marked brighter is your own. Everyone else's sits plain.",
    "crew.targets.links": "{n} to link up",
    "crew.legend.fresh": "Taken this week",
    # The one concrete clock in the feature. It was phrased as a question about the list it
    # sat under -- "Rode one of these already?" -- which made it meaningless on a card with
    # no rows, and buried it on two cards a rider only reaches by scrolling. Four reviewers
    # in a row said the stakes are stated but never SCHEDULED: nothing anywhere said when a
    # ride you just finished turns into ground. This does, and it stands on its own.
    "crew.drawn.in": "Ride now and it lands on the map in about {n} min.",
    "crew.drawn.soon": "The map is redrawing right now. New ground lands any second.",
    # When the rebuild is more than an interval late. "New ground lands any second" was
    # printed by every value <= 1 minute, so a map seven hours stale said it for six of
    # them and would have said it for ever. This also answers the question the panel never
    # did: not when the next one lands, but how old the one you are reading is.
    # The diff the crew card leads with. Five reviewers said the mode is entirely pull --
    # every piece of news exists and none of it is told to you -- so this is the panel paying
    # you for opening it. Held per browser; it is one reader's "last time I looked".
    "crew.since.h": "Since you last looked:",
    # The dock badge. Not a count of anything -- one fact -- so it shows as a bare dot and
    # this is what it says on hover and to a screen reader.
    "crew.since.dot": "Something moved in your crew",
    # Good news, with the weight the bad news has always had. Every one-shot card in this mode
    # announces something going wrong -- turned down, removed, folded -- and taking ground
    # showed up only as a grey line in a corner. A reviewer put it plainly: nothing ever
    # congratulates you. Same slot, same card shape, same prominence; it just has to be true,
    # so it appears only when the crew actually gained.
    "crew.good.h": "You took ground",
    "crew.good.p": "{v} since you last looked.",
    "crew.since.up": "+{v}",
    "crew.since.down": "−{v}",
    "crew.since.rose": "{a} → {b}",
    "crew.since.fell": "{a} → {b}",
    "crew.drawn.old": "This map was drawn over {n} h ago. New rides are waiting on the next redraw.",
    "crew.targets.takenby": "somebody holds it",
    "crew.targets.drops": "drops them to {n}",
    "crew.targets.passes": "puts them behind {name}",
    "crew.targets.youpass": "takes you past them",
    "crew.targets.flips": "comes off {name} when the block lands",
    "crew.targets.got": "done",
    "crew.targets.p0one": "One square to go. Ride {v} inside the marked one and you are on the map.",
    "crew.targets.p0n": "{s} to go, {v} between them, and you are on the map.",
    "crew.targets.p0done": "Your block is ridden. It shows up on the next redraw.",
    "crew.targets.far": "out of reach for now",
    "crew.decl.h": "Turned down lately",
    "crew.decl.undo": "Let them in",
    "crew.decl.gone": "in another crew now",
    # Third person and lowercase, like the line above it. The declines column
    # used to borrow the join card's heading, which addresses the rider it is
    # talking to -- so a leader was told to cool down about somebody else.
    "crew.decl.cooling": "cooling off now",
    # The third reason letting somebody in can fail, and the one the row used
    # to leave out: without it `why` fell through to "in another crew now"
    # about a rider who had joined nobody.
    "crew.decl.full": "no room right now",
    "crew.who.share": "{n}%",
    "crew.rank.top": "top of the board",
    "crew.rank.nth": "{n}th",
    # `{n}` comes through `tiles()`, so the noun agrees. As a bare integer this was the one
    # count in the feature with nothing after it for a locale to inflect, in the most
    # prominent line on the crew card.
    "crew.rank.off": "{n} off {v}",
    "crew.rank.level": "level with {v}",
    "crew.roles.remove": "Remove",
    "crew.roles.removeq": "Take {name} off the crew? Their old rides stay on the map.",
    # The refusal asks first. It was the one consequential control in the feature with no
    # guard -- "Let in" and "No" are 8px apart, both 44px tall, and No was instant and
    # final. It says they can come back, because they can: `decide(accept=True)` reopens
    # a refusal for a week, which is what the un-decline row above is for.
    "crew.roles.declineq": "Turn {name} away? You can let them in later if you change your mind.",
    "crew.removed.h": "You are out",
    "crew.removed.p": "{name} took you off the crew. No waiting, join another whenever you like.",
    # the server has written the better sentence since this code existed; the
    # panel was printing the generic failure over the top of it
    # Pressing the affirmative on an empty box. It used to run the CLOSE path, which threw
    # away the prompt and your place in the list without a word.
    "crew.e.empty": "Type it in first.",
    "crew.e.wrongscreen": "That code is for the admin screen, not this one.",
    "crew.e.expired": "That code has run out. Here is a fresh one.",
    "crew.e.norider": "The app has not sent us a ride yet. Upload one and try again.",
    "crew.e.busy": "Too many people pairing at once. Try again in a minute.",
    "crew.e.not_yourself": "Use {v} for that.",
    "crew.folded.h": "That crew is gone",
    "crew.folded.p": "{name} folded. Your old rides stay with it, and you can join another right away.",
    "crew.declined.h": "They said no",
    "crew.declined.p": "{name} turned your request down. No waiting, pick another one.",
    "crew.join.away": "{v} from here",
    "crew.join.full": "Full",
    "crew.join.nomatch": "Nothing matches “{v}”.",
    "crew.join.showall": "Show all",
    "crew.join.wait.btn": "Cooling off",
    "crew.off.h": "Crews are off",
    "crew.e.off": "Crews are switched off right now.",
    "crew.e.leaderback": "Your leader turned up again.",
    "crew.e.notyou": "The crew's longest-serving rider takes over, and that is not you.",
    "crew.e.left": "You are not in that crew.",
    "crew.e.norequest": "That request is already dealt with.",
    "crew.e.full": "That crew is full. Pick another.",
    "crew.e.closed": "Not taking new crews right now.",
    "crew.e.forbidden": "Only a leader or officer can do that.",
    "crew.e.pass": "Your pass ran out. Grab a new one.",
    # "Slow down a second." against a 30-per-hour window understated the wait by three
    # orders of magnitude, and `offerRetry` put a "Get a fresh code" button under it that
    # walked straight back into the same 429. The limit is per IP, so a household or a cafe
    # behind one address spends each other's allowance -- which is worth saying, because
    # otherwise the reader has no way to work out why they are locked out at all.
    "crew.e.rate": "Too many tries from this network. Give it a while.",
    # The server sends the figure now. Two units in the shapes `crew.drawn.in` and
    # `crew.drawn.old` already use for minutes and hours.
    "crew.e.rate.in": "Too many tries from this network. Try again in about {n} min.",
    "crew.e.rate.inh": "Too many tries from this network. Try again in about {n} h.",
    # The server's `not_member` sentence is written for the rider it is ABOUT -- 
    # "You are not in that crew." -- and a leader removing somebody saw it addressed
    # to themselves while they plainly were in the crew. This speaks to the reader.
    "crew.e.stale": "That had already changed. Here it is as it stands now.",
    "crew.e.invite": "That code is not it.",
    # The actionable half. "That code is not it." names the problem and stops, with no hint
    # where a code comes from -- a reviewer called it a dead end. Its own key rather than a
    # rewrite of the sentence above, so eighteen existing translations stay as they are.
    "crew.e.invite.ask": "Ask the crew for theirs.",
    "crew.e.name": "3 to 28 characters, and nothing exotic.",
    # The client checks the same shape the server does, so a typo costs a keystroke
    # instead of a round trip -- and says WHICH half failed. One sentence used to
    # answer an empty box, three spaces, two emoji, "ab" and `<b>hi</b>` alike.
    "crew.e.name.empty": "Give it a name.",
    "crew.e.name.short": "Three characters at least.",
    "crew.e.name.long": "Twenty-eight characters at most.",
    "crew.e.name.chars": "Letters, numbers, spaces and - ' & . only.",
    # "..." is three permitted characters, so the sentence above answered a name made only of
    # dots by listing the dot as allowed. The rule it breaks is a different one -- a name has
    # to contain something you could read it by -- and it needs to say so.
    "crew.e.name.word": "A name needs at least one letter or number.",
    # A crew named `admin` is refused by `name_reserved`, and the client had no entry for
    # the code -- so the one refusal added to stop impersonation answered "That did not
    # work.", the only message in this form that explains nothing. Reviewer R found it by
    # noticing that every OTHER name error names the rule it broke.
    "crew.e.name.reserved": "That name is reserved. Pick something that is yours.",
    "crew.mine.saved": "Saved",
    # Three successes that said nothing: approving somebody (the knock row vanishing
    # was the only evidence a person had joined), turning them down, and disbanding.
    "crew.roles.letin": "{name} is in.",
    "crew.roles.turned": "{name} turned down.",
    "crew.mine.disbanded": "{name} is gone. Its ground fades off the map on its own.",
    "crew.e.taken": "That name's taken.",
    "crew.e.increw": "Leave your crew first.",
    "crew.e.notrips": "Send up one ride first.",
    "crew.e.cooldown": "Still cooling off from the last one.",
    "crew.e.identity": "Another crew already flies those colours.",
    "crew.e.gone": "That crew is gone.",
    "crew.e.promote": "Make somebody an officer first. Someone has to run the place.",
    "crew.e.image2": "Too busy to shrink. Try something flatter, with fewer colours.",
    "crew.e.image": "That picture will not do. A small square PNG, under 2 MB.",
    "crew.mine.invite": "Invite code",
    # Eight hex characters a new leader had to select by hand, inside a
    # panel that scrolls under your finger.
    "crew.mine.copy": "Copy",
    "crew.mine.copylink": "Copy link",
    "crew.mine.copied": "Copied",
    "crew.mine.settings": "Crew settings",
    "crew.mine.emblem": "Emblem (small, square)",
    # The colours are a guess until the crew is on the map and a fact afterwards, so the
    # picker goes away at the moment the rectangles appear. Doubles as the refusal if a
    # request tries anyway.
    "crew.mine.colourlock": "Your colours are on the map now, so they stay as they are.",
    "crew.mine.emblemp": "Leave it empty and we draw one from your name.",
    "crew.mine.generated": "Use the drawn one",
    "crew.mine.save": "Save",
    "crew.mine.leave": "Leave crew",
    "crew.mine.signout": "Sign out",
    "crew.mine.leaveq": "Leave {name}? No new crew for {n}.",
    # A leader walking out of a crew that carries on. `leave()` promotes nobody -- the crew is
    # left with officers and no leader until one of them takes it over through "Take the crew
    # over?" -- and the prompt told them only about their own seven days, word for word what a
    # plain member reads, while a crew with riders and ground was about to be left in charge of
    # nobody. The reader is the one person who can prevent that by handing it over first.
    "crew.mine.leaveq.lead": "Leave {name}? It is left with no leader until an officer takes it over, and you wait {n} before joining another.",
    # Walking out of a crew you are the last member of ends it. `leave()` retires the clan
    # when nobody active is left AND stamps the cooldown, so the gentle-sounding button
    # destroys the crew and benches you a week, while Disband does it for nothing.
    "crew.mine.leaveq.last": "Leave {name}? You are the last one in it, so the crew ends with you — and you wait {n} before joining another. Disbanding it costs you no wait at all.",
    "crew.role.leader": "leader",
    "crew.role.officer": "officer",
    "crew.role.member": "member",
    "crew.role.past": "left",
    # `/crews/me` answers `role: "member"` with `status: "pending"`, so a rider still
    # knocking had MEMBER written over a card saying "Waiting on a leader to let you in".
    "crew.role.waiting": "waiting",
    "crew.accept": "Let in",
    "crew.decline": "No",
    "crew.pending.h": "Knocking",
    # how it works
    "crew.how.h": "How crews work",
    # What the mode IS, before any of the rules for playing it. The manual opened on "Ride it,
    # it turns your colour", which is a rule and not an answer.
    "crew.how.intro": "The world is carved into squares. Ride the same ones harder than anyone else and they turn your crew's colour. Hold a block of them and your crew is on the map, with every other crew out there coming to take it.",
    "crew.how.s1": "Conquering territory",
    "crew.how.s2": "Losing ground",
    "crew.how.s3": "Your crew",
    "crew.how.c4": "Surrounded. You never rode this one. You rode all the way around it.",
    "crew.how.c5": "Taken this week. Somebody just turned it over, and it is the newest thing on the map.",
    "crew.how.c3": "Going cold. Nobody has been back, so it sits at its floor. It stays yours until somebody rides it.",
    "crew.how.c2": "About to flip. One more ride by them and it changes hands.",
    # No unit. This and `crew.how.1` below used "km" as prose for "distance ridden", with no
    # placeholder, so they could never convert and were simply wrong for a reader in miles --
    # and once `crew.how.8` learned to convert they got worse, because the same modal then read
    # "only put 3.1 mi a week" two lines from "the most km into it". Both sentences mean "rode
    # it more than anyone else", which is true in every unit, so the version that never raises
    # the question is the right one.
    "crew.how.c1": "Contested. Another crew is riding it too.",
    "crew.how.c0": "Uncontested. Nobody else has put a wheel in it lately.",
    "crew.how.s4": "What the colours mean",
    "crew.how.s5": "What the numbers mean",
    "crew.how.n1": "The board ranks a crew on its biggest patch in one piece, not on everything it holds. Two patches of ten lose to one of eleven.",
    # This said "squares and km² are the same thing counted twice" and then, in the same
    # breath, that squares differ by latitude -- and closed with "SO a square is the same
    # amount of riding everywhere", which inverts its own inference: a wider square takes
    # MORE riding to cross, not the same. A reviewer did the arithmetic the sentence invites
    # and got the wrong answer: Equator Express holds 14 squares over 83.7 km² (2.44 km a
    # side) against Nordlys Collective's 15 squares over 22.6 km² (1.24 km a side) -- same
    # unit, four times the ground. What IS constant is the number of crossings, because the
    # weekly cap scales with the square; the distance those crossings add up to is not.
    "crew.how.n2": "A square is about {v} across where YOU ride — wider nearer the equator, narrower nearer the poles. Your weekly limit inside one scales with it, so filling a square always takes four to six crossings; nearer the equator those crossings are longer, so it is more riding and more ground.",
    # Both from the settings that enforce them. The cooldown was only ever said in the leave
    # prompt, where you read it after deciding; the size cap was never said anywhere.
    # The server blocks FOUNDING as well -- `can_found` goes false and the whole START A
    # CREW form is removed during the wait. A reader who left intending to start their
    # own was told that was allowed and then found it was not.
    "crew.how.cool": "Walk out of a crew and you wait {v} before joining another one or starting your own.",
    "crew.how.size": "A crew holds {n} riders at most.",
    "crew.how.1": "Ride a square and it turns your colour. Whoever rode it most over the last {d} days holds it.",
    # "a 2x2 block, about 1.2 km across" -- `{v}` is the width of ONE square, so that
    # sentence put a 2x2 block at the size of a single square, and the numbers section
    # printed the same figure for one square two sections later. "each" fixes it.
    "crew.how.2": "Nothing shows until you hold a {n}x{n} block of them, each about {v} across. One street gets you nothing.",
    "crew.how.3": "Ground grows out of ground. Close the gap between two patches and they count as one, which is the biggest move there is. Ride a full loop around something and the inside is yours too, up to about double what you rode.",
    "crew.how.4": "Nothing holds itself. Stop riding and your claim fades until one ride is enough to take it off you, but nobody takes it by waiting. Ride more than someone and you take theirs.",
    "crew.how.5": "Rides you did for a crew stay with that crew. Walking out does not wipe the map.",
    "crew.how.roles": "Whoever starts a crew runs it. They can make anyone an officer, and an officer can let riders in and turn them away — but only the leader changes the crew itself, or ends it.",
    "crew.how.7": "A block needs its {n}x{n}. Take the square holding one together and everything leaning on it goes down with it.",
    "crew.how.6": "A new crew's first fortnight counts what its riders were already doing, so nobody starts on an empty map.",
    # `{c}` carries its own unit now. It used to be a bare number with "km" written into the
    # sentence, so a reader in miles was told "a square is about 0.7 mi across" two lines above
    # "one rider can only put 5 km a week into one square" -- the one hard figure in the manual
    # stated in a unit that reader had switched away from. The number was right; the label lied.
    "crew.how.8": "One rider can only put {c} a week into one square, however far they go. So a square goes to whoever brings more people: while two of them keep riding it, one cannot out-ride them. If they stop, it fades like anything else.",
    "crew.empty": "Nobody holds anything yet.",
    # The second half claimed a square is the same ride anywhere, which the board directly
    # below it disproves -- and it is the board's OWN subtitle. The honest version names the
    # unit it ranks on and admits what that unit does not control for.
    "crew.board.sub": "Biggest patch a crew holds in one piece, counted in squares — and a square covers more ground nearer the equator.",
    "crew.signin.noapp": "No app yet? Get EUC Planet.",
    "crew.mine.invite2": "Invite link code",
    "crew.mine.leaveq0": "Leave {name}? You can join another one straight away.",
    "crew.days.few": "{n} days",
    "crew.days": "{n} days",
    "crew.day1": "{n} day",
    "crew.now": "right now",
    "crew.signin.again": "Still waiting? Get a fresh code",
    # Arriving from a crew's poster signed out landed on the generic GET STARTED card with no
    # mention of the crew whose sticker you had just scanned. The intent survived the pairing
    # -- `offerInvite` scrolls to that crew and flashes its row -- but the screen that asks
    # you to go and fetch your phone had forgotten why you were there.
    "crew.signin.invited": "You came here for {name}. Pair once and we will take you straight to it.",
    "crew.signin.ok": "You're in",
    "crew.mine.youare": "you run it",
    "crew.mine.youofficer": "you are an officer",
    "crew.mine.youmember": "you ride for them",
    "crew.riders.few": "{n} riders",
    "crew.riders": "{n} riders",
    "crew.rider1": "{n} rider",
    "crew.policy.open": "anyone can join",
    "crew.policy.approval": "leader says yes",
    "crew.policy.invite": "invite code",
    # The public crew page at /c/<slug> and the Share card that hands out its address. This
    # one is meant to be PRINTED -- on a backpack, on a sticker -- so it points at the slug
    # and never at the invite code: a code can be rotated, and a sticker cannot.
    # The two actions on the popup you get by tapping a square on the map. Not behind a hover
    # delay: a phone has no hover, and a long press is the OS's own gesture.
    # The collapsed strip on the map that explains the colours. One word, because it sits in
    # a 230px box over the map and competes with the map for attention.
    "crew.key": "Key",
    "crew.pop.highlight": "Highlight",
    "crew.pop.details": "Details",
    "crew.pub.what": "Crews cut the map into squares. Ride inside one and it turns your crew's colour.",
    "crew.pub.scan": "Scan to find this crew",
    "crew.pub.open": "Open in EUC Stats",
    # What the button on a crew's own page says, chosen by how that crew lets people in.
    # It used to say "Open in EUC Stats" for all three, on a page whose whole job is
    # recruitment and which states "anyone can join" two lines above it.
    "crew.pub.join": "Join this crew",
    "crew.pub.ask": "Ask to join",
    "crew.pub.folded": "This crew has folded.",
    "crew.pub.print": "Printable code",
    "crew.share": "Share crew",
    "crew.share.p": "Print it, stick it on your backpack. It points at the crew and not at a code, so a new code does not kill it.",
    "crew.roles.h": "The crew",
    # The roster's fold. NOT `crew.join.all`, which folds a list of CREWS: Polish wants the
    # masculine-personal "wszystkich" for people and Russian the animate "всех", and the
    # guard caught the reuse before either shipped.
    "crew.roles.all": "Show all {n}",
    "crew.roles.promote": "Make officer",
    "crew.roles.demote": "Stand down",
    "crew.mine.signoutq": "Sign out? You need your phone to get back in.",
    # What each of these actually does, in plain words, for the hover and for a screen reader.
    # The labels stay in this feature's voice; these say the same thing with nothing in it to
    # work out. Deliberately not shown as body text: six explanatory lines under six buttons
    # is a card nobody reads, and the question "what does this one do" is asked one button at
    # a time.
    "crew.tip.leave": "You stop riding for this crew. The rides you already did stay on the map.",
    "crew.tip.cancel": "Takes your request back. You are not in the crew either way.",
    "crew.tip.disband": "Ends the crew for everyone in it. This cannot be undone.",
    # "Copy link" and "Copy" sat side by side with nothing on either saying which was which.
    "crew.tip.copylink": "A link that opens this crew with the code already filled in. Good for one person, today.",
    "crew.tip.copycode": "Just the code, for reading out loud or typing in by hand.",
    # Turning the invite code over. It was generated once and could never be changed, so a
    # code in an old screenshot let somebody into an invite-only crew for ever.
    "crew.mine.newcode": "New code",
    "crew.tip.newcode": "Retires the old code. Anyone still holding it is locked out.",
    "crew.mine.newcodeq": "Make a new invite code? Every link and screenshot of the old one stops working.",
    "crew.mine.newcoded": "New code. The old one is dead.",
    "crew.tip.claim": "Makes you the leader, because the one you have has gone quiet.",
    "crew.tip.signout": "Signs this browser out. The crew carries on without you here, and you need your phone to get back in.",
    "crew.tip.remove": "Takes this rider out of the crew. They can ask to join again.",
    # The knock count, on the dock badge and on the shut crew card. A number on its own says
    # how many of something it never names.
    "crew.knock1": "One rider wants to join your crew.",
    "crew.knocks.few": "{n} riders want to join your crew.",
    "crew.knocks": "{n} riders want to join your crew.",
    # Disbanding asks for the name. Everything else here can be undone by doing it again --
    # you can re-join, re-ask, sign back in -- and this one ends a thing other people rode for,
    # so it is the one place worth making somebody prove they meant it.
    "crew.mine.disbandtype": "Type {name} to end it.",
    "crew.e.nomatch": "That is not the name, so nothing was ended.",
    "crew.closed.h": "Not taking new crews",
    "crew.closed.p": "New crews are off for now. Join one instead.",
    "crew.err": "That didn't work.",
    # what a tile says when you tap it
    "crew.tile.safe": "Uncontested",
    "crew.tile.pushed": "Contested",
    "crew.tile.slipping": "About to flip",
    "crew.tile.free": "Up for grabs",
    "crew.tile.fading": "Going cold",
    "crew.tile.ringed": "Surrounded",
    "crew.tile.ringedp": "taken by riding right round it",
    "crew.tile.clear": "{v} ahead of anyone else",
    "crew.targets.p0": "You are not on the map yet. The marked squares are one {n}x{n} block, the cheapest one you can finish. Ride all of them.",
    "crew.lose.p3": "Nobody is taking these off you. You just stopped turning up.",
    # panel titles (header of the sliding panel)
    "title.riders": "Riders",
    "title.crews": "Crews & Territory",
    "title.countries": "Countries",
    "title.wheels": "Wheel models",
    "title.brands": "Wheel brands",
    "title.records": "All-time records",
    "title.tech": "App & OS",
    # aria / tooltips
    "aria.settings": "Settings",
    "act.refresh": "Refresh",
    # cogwheel settings menu
    "cfg.units": "Units",
    "u.metric": "Metric",
    "u.imperial": "Imperial",
    "cfg.map": "Map",
    "cfg.intro": "Intro",
    "cfg.language": "Language",
    "cfg.enabled": "Enabled",
    "cfg.replay": "Replay",
    "cfg.intro_off": "Disabled site-wide",
    "cfg.intro_play": "Play the cinematic intro on load",
    # map styles
    "map.dark": "Dark",
    "map.light": "Light",
    "map.voyager": "Voyager",
    "map.satellite": "Satellite",
    "map.topo": "Topo",
    # footer
    "foot.poweredby": "powered by",
    # top stat chips
    "chip.riders": "Riders",
    "chip.trips": "Trips",
    "chip.total": "Total {unit}",
    "chip.countries": "Countries",
    # champions strip
    "champ.title": "EUC Planet Champions",
    "champ.day": "Day",
    "champ.week": "Week",
    "champ.month": "Month",
    "champ.pts": "{n} pts",
    "champ.norides": "no rides yet",
    "champ.toggle": "Show / hide",
    "champ.tip": "Our secret recipe: distance is king, lifted by your top speed and time on the wheel.",
    # empty / error states
    "empty.nodata": "no data yet",
    "empty.norecords": "no records yet",
    "empty.noapp": "no app data yet",
    "empty.apierror": "API error",
    # podium ranks (very short)
    "pod.1": "1ST",
    "pod.2": "2ND",
    "pod.3": "3RD",
    # generic units / sub-labels
    "u.riders": "{n} riders",
    "u.activeRiders": "Active riders",
    "u.ridesLogged": "Rides logged",
    "g.riders": "Riders",
    "g.rides": "Rides",
    # tech / app panel section titles (emoji kept, words translated)
    "tech.adoption": "Adoption",
    "tech.adoptionPct": "{pct}% of riders on the latest app · v{ver}",
    "tech.adopters": "Bleeding Edge · newest app",
    "tech.laggards": "Living in the past · oldest app",
    "tech.appvers": "App versions",
    "tech.osvers": "OS versions",
    "tech.countries": "Up-to-date countries",
    # all-time record labels
    "rec.mileage_king": "Mileage King",
    "rec.top_speed": "Top Speed",
    "rec.longest_trip": "Longest Trip",
    "rec.max_gforce": "Max G-Force",
    "rec.sustained_w": "Sustained Power",
    "rec.sustained_a": "Sustained Current",
    "rec.peak_voltage": "Voltage Peak",
    "rec.max_altitude": "Highest Altitude",
    "rec.min_altitude": "Lowest Altitude",
    "rec.biggest_climb": "Biggest Climb",
    "rec.biggest_downhill": "Biggest Downhill",
    "rec.most_rides": "Most Rides",

    # --- leaderboard trophies: NAME (creative, EUC-flavoured) + DESC (factual) ---
    "b.mileage.n": "Most Distance",
    "b.mileage.d": "Most distance ever ridden",
    "b.daily.n": "Best Day",
    "b.daily.d": "Most distance in a single day",
    "b.week.n": "Best Week",
    "b.week.d": "Most distance in one week",
    "b.month.n": "Best Month",
    "b.month.d": "Most distance in one calendar month",
    "b.speed.n": "Top Speed",
    "b.speed.d": "Highest speed reached on any ride",
    "b.accel.n": "0→40 km/h",
    "b.accel.d": "Fastest launch from a stop to 40 km/h (≈25 mph) · lower is better",
    "b.accel.d_mph": "Fastest launch from a stop to ≈25 mph (40 km/h) · lower is better",
    "b.gforce.n": "Top G",
    "b.gforce.d": "Strongest g-force spike",
    "b.power.n": "Power 2s",
    "b.power.d": "Highest power held for 2 seconds",
    "b.current.n": "Current 2s",
    "b.current.d": "Highest current held for 2 seconds",
    "b.voltage.n": "Peak Voltage",
    "b.voltage.d": "Highest battery voltage observed",
    "b.streak.n": "Streak Master",
    "b.streak.d": "Longest run of consecutive days ridden",
    "b.ascent.n": "Total Climb",
    "b.ascent.d": "Total elevation climbed (Everest = 8849 m)",
    "b.range.n": "Best Range",
    "b.range.d": "Longest estimated full-charge range",
    "b.efficiency.n": "Efficiency",
    "b.efficiency.d": "Lowest energy use per km · most efficient",
    "b.hours.n": "Steel Legs",
    "b.hours.d": "Most hours on the wheel",
    "b.cruise.n": "Calm Ride",
    "b.cruise.d": "Longest calm ride held under 10 km/h",
    "b.globe.n": "Globe Trotter",
    "b.globe.d": "Most countries ridden in",
    "b.altking.n": "Altitude King",
    "b.altking.d": "Biggest altitude swing in one ride",
    "b.frequent.n": "Most Rides",
    "b.frequent.d": "Most rides logged",
    "b.marathon.n": "Longest Ride",
    "b.marathon.d": "Longest single ride by time",
    "b.pace.n": "Avg Speed",
    "b.pace.d": "Highest average speed on a single ride",
    "b.battery.n": "Battery Drain",
    "b.battery.d": "Biggest battery drain in one ride",
    "b.night.n": "Night Rider",
    "b.night.d": "Most rides started at night (22:00–05:00 UTC)",
    "b.weekend.n": "Weekend Warrior",
    "b.weekend.d": "Most distance ridden on weekends",
    "b.early.n": "Early Bird",
    "b.early.d": "Most rides started in the morning (05:00–09:00 UTC)",
    "b.peak.n": "Biggest Climb",
    "b.peak.d": "Biggest elevation gain in a single ride",
    "b.energy.n": "Most Energy",
    "b.energy.d": "Most total energy used across all rides",
    "b.explorer.n": "Explorer",
    "b.explorer.d": "Most distinct map areas ridden",
    "b.bigday.n": "Busiest Day",
    "b.bigday.d": "Most rides in a single day",
    "b.commuter.n": "Commuter",
    "b.commuter.d": "Most distance ridden on weekdays",
    "b.freespin.n": "Biggest Freespin",
    "b.freespin.d": "Highest speed a wheel reached while spinning free — one spike, in km/h",
    "b.cutouts.n": "Falls / Cutouts",
    "b.cutouts.d": "Most falls detected — riding, then the wheel spinning free and the ride ending",
    "b.spins.n": "Freespins",
    "b.spins.d": "Most times the wheel was spun up off the ground — harmless, just for fun",
    "b.cutout.n": "Falls / Cutouts",
    "b.sag.n": "Voltage Sag",
    "b.sag.d": "Biggest voltage drop under load — the hardest battery pull",
    "b.rocket.n": "Rocket",
    "b.rocket.d": "Hardest sustained acceleration held for 2s+",
    # new gated / extreme boards (ship disabled). The qualifying ride length + distance
    # is appended on the client, unit-aware, so it's not part of the description string.
    "b.temphigh.n": "High Temp",
    "b.temphigh.d": "Hottest the board ever ran",
    "b.templow.n": "Low Temp",
    "b.templow.d": "Coldest ride",
    "b.temprise.n": "Temp Climb",
    "b.temprise.d": "Fastest the board heated up while riding (deg/s)",
    "b.tempdrop.n": "Temp Drop",
    "b.tempdrop.d": "Fastest the board cooled down while riding (deg/s)",
    "b.pwm.n": "Peak PWM",
    "b.pwm.d": "Closest to maxing the motor (PWM)",
    "b.battlow.n": "Low Battery",
    "b.battlow.d": "Lowest battery % reached",
    "b.althigh.n": "Max Altitude",
    "b.althigh.d": "Highest altitude ever reached",
    "b.altlow.n": "Min Altitude",
    "b.altlow.d": "Lowest altitude ever reached",
    # newer hidden metrics: longer sustained windows, high-speed / directional g, shake
    "b.g4.n": "G-Hold 4s",
    "b.g4.d": "Highest g-force held for 4 seconds",
    "b.g6.n": "G-Hold 6s",
    "b.g6.d": "Highest g-force held for 6 seconds",
    "b.pwm3.n": "PWM 3s",
    "b.pwm3.d": "Highest PWM held for 3 seconds",
    "b.spd5.n": "Speed 5s",
    "b.spd5.d": "Highest speed held for 5 seconds",
    "b.spd10.n": "Speed 10s",
    "b.spd10.d": "Highest speed held for 10 seconds",
    "b.pw6.n": "Power 6s",
    "b.pw6.d": "Highest power held for 6 seconds",
    "b.cur6.n": "Current 6s",
    "b.cur6.d": "Highest current held for 6 seconds",
    "b.gf20.n": "Grip G @20",
    "b.gf20.d": "Strongest g-force sustained above 20 km/h (≈12 mph)",
    "b.gf30.n": "Grip G @30",
    "b.gf30.d": "Strongest g-force sustained above 30 km/h (≈19 mph)",
    "b.gf40.n": "Grip G @40",
    "b.gf40.d": "Strongest g-force sustained above 40 km/h (≈25 mph)",
    "b.shake.n": "Wobble",
    "b.shake.d": "Biggest speed-wobble / shake index (experimental)",
    "b.accg.n": "Accel G",
    "b.accg.d": "Hardest acceleration as a g-force (from speed)",
    "b.brkg.n": "Braking G",
    "b.brkg.d": "Hardest braking as a g-force (from speed)",
    "b.sprint60.n": "0→60",
    "b.sprint60.d": "Fastest launch from a stop to 60 km/h (≈37 mph) · lower is better",
    "b.sprint100.n": "0→100",
    "b.sprint100.d": "Fastest launch from a stop to 100 km/h (≈62 mph) · lower is better",
    "b.acc30.n": "Accel 30+",
    "b.acc30.d": "Hardest acceleration g while already above 30 km/h (≈19 mph)",
    "b.acc50.n": "Accel 50+",
    "b.acc50.d": "Hardest acceleration g while already above 50 km/h (≈31 mph)",
    "b.brk30.n": "Brake 30+",
    "b.brk30.d": "Hardest braking g coming down from 30 km/h (≈19 mph)",
    "b.brk50.n": "Brake 50+",
    "b.brk50.d": "Hardest braking g coming down from 50 km/h (≈31 mph)",
    "b.stop30.n": "Stop 30",
    "b.stop30.d": "Fastest stop from 30 km/h (≈19 mph) to a standstill · lower is better",
    "b.stop50.n": "Stop 50",
    "b.stop50.d": "Fastest stop from 50 km/h (≈31 mph) to a standstill · lower is better",

    # --- group-panel boards (countries / wheels / brands) descriptions ---
    "g.dist.d": "Most distance ridden",
    "g.speed.d": "Fastest ride in the group",
    "g.accel.d": "Fastest 0→40 km/h · lower is better",
    "g.gforce.d": "Strongest g-force spike",
    "g.power.d": "Highest power held 2s",
    "g.current.d": "Highest current held 2s",
    "g.voltage.d": "Highest battery voltage",
    "g.riders.d": "Active riders",
    "g.trips.d": "Rides logged",
    "g.ascent.d": "Total elevation climbed",
    "g.range.d": "Longest est. range",
    "g.eff.d": "Lowest Wh/km · best",
    "g.cutout.d": "Cutouts per 1000 km ridden",
    "g.climb.d": "Biggest single climb",
    "g.alt.d": "Highest altitude reached",
    "g.temp.d": "Hottest the board ran",
}

# Native names shown in the language selector (never translated).
LANG_NAMES: dict[str, str] = {
    "en": "English", "da": "Dansk", "de": "Deutsch", "es": "Español",
    "es-419": "Español (LatAm)", "fr": "Français", "it": "Italiano",
    "nl": "Nederlands", "no": "Norsk", "pl": "Polski", "pt-BR": "Português (BR)",
    "ru": "Русский", "sv": "Svenska", "uk": "Українська",
    "zh": "中文 (简体)", "zh-Hant": "中文 (繁體)", "ja": "日本語", "ko": "한국어",
    "tr": "Türkçe",
}

# Filled by the translation workflow (web/i18n_data.py). Imported lazily so the
# module loads even before translations exist.
try:
    from web.i18n_data import TRANSLATIONS  # type: ignore
except Exception:  # pragma: no cover - until translations are generated
    try:
        from i18n_data import TRANSLATIONS  # type: ignore
    except Exception:
        TRANSLATIONS = {}


def langs_payload() -> dict:
    """{locale: {key: text}} for every supported locale, English-complete."""
    out = {"en": EN}
    for loc, table in (TRANSLATIONS or {}).items():
        if loc == "en":
            continue
        # English fallback per key keeps the client simple and crash-proof
        out[loc] = {k: (table.get(k) or v) for k, v in EN.items()}
    return out


def locale_table(loc: str) -> dict | None:
    """One locale's English-complete table, or None if unknown. Lazy-loaded by the
    client for any language beyond the one injected on first paint."""
    if loc == "en":
        return EN
    table = (TRANSLATIONS or {}).get(loc)
    return {k: (table.get(k) or v) for k, v in EN.items()} if table is not None else None


def pick(accept_language: str) -> str:
    """Best supported locale for an Accept-Language header (a server-side mirror of the
    client's language mapping). Lets the page inject just EN + the visitor's language
    instead of all of them."""
    sup = set(LANG_NAMES)
    for part in (accept_language or "").split(","):
        code = part.split(";")[0].strip().lower()
        if not code:
            continue
        base = code.split("-")[0]
        reg = code.split("-")[1] if "-" in code else ""
        if base == "pt":
            loc = "pt-BR"
        elif base == "zh":
            loc = "zh-Hant" if any(x in code for x in ("tw", "hk", "mo", "hant")) else "zh"
        elif base in ("nb", "nn", "no"):
            loc = "no"
        elif base == "es":
            lat = {"419", "mx", "ar", "co", "cl", "pe", "ve", "ec", "gt", "cu",
                   "bo", "do", "hn", "py", "sv", "ni", "cr", "pa", "uy", "pr"}
            loc = "es-419" if reg in lat else "es"
        else:
            loc = base
        if loc in sup:
            return loc
    return "en"
