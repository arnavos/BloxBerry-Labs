import asyncio
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import json
import os
import re
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands

# ============================================================
# BLOXBERRY LABS - ALL-IN-ONE DISCORD BOT
# ============================================================
# Install: pip install -r requirements.txt
# Set TOKEN below, then run: python bot.py
# Keep this token private. Regenerate it if it was ever exposed.

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

# Channels / categories
MAIN_RECRUITMENT_CHANNEL_ID = 1571114752653131836
SKILL_RECRUITMENT_CHANNEL_ID = 1571128273344401508
APPLICATION_REVIEW_CHANNEL_ID = 1571114159548923948

TICKET_PANEL_CHANNEL_ID = 1567899862022758564
TICKET_CATEGORY_ID = 1571094794933895241
TRANSCRIPT_CHANNEL_ID = 1571110540414292139
WELCOME_CHANNEL_ID = 1567899844062740510

# Roles
AUTO_MEMBER_ROLE_ID = 1568182409957081108
PENDING_APPLICATION_ROLE_ID = 1571120110830100652
TICKETS_ACCESS_ROLE_ID = 1571063785081536562
PURGE_AUTH_ROLE_ID = 1567599630030610502

# Role allowed to review recruitment applications
RECRUITMENT_REVIEWER_ROLE_IDS = {
    1571062526232236223,  # Studio Director
    1571065103413944401,  # Executive Manager
    1571065681670180975,  # Project Manager
    1571065040390586409,  # Technical Director
    1571063896977055855,  # Creative Director
    1571064979686432869,  # Community Director
}

# Main recruitment positions
MAIN_ROLES = {
    "Executive Manager": 1571065103413944401,
    "Project Manager": 1571065681670180975,
    "Technical Director": 1571065040390586409,
    "Creative Director": 1571063896977055855,
    "Game Design Lead": 1571065144010743818,
    "Art Director": 1571063939213566044,
    "Community Director": 1571064979686432869,
    "Marketing Team": 1571065232778989679,
    "Social Media Manager": 1571071998086611064,
}
SEAT_LIMITS = {
    "Executive Manager": 1,
    "Project Manager": 2,
    "Technical Director": 2,
    "Creative Director": 2,
    "Game Design Lead": 2,
    "Art Director": 1,
    "Community Director": 1,
    "Marketing Team": 5,
    "Social Media Manager": 2,
}

# Skill-based recruitment roles
SKILL_ROLES = {
    "AI Lead Developer": 1571068709454348328,
    "Programmers": 1571068590235328563,
    "Game Testers": 1571078210907668480,
    "UI Designer": 1571068770112512000,
    "3D Model Designer": 1571068769978163250,
    "Animator": 1571068771911602196,
    "Character Designer": 1571074678481485954,
    "Map Designer": 1571071574751313972,
    "Texture Artist": 1571071230545625098,
    "VFX Artist": 1571071267723939941,
    "Community Mods": 1571071401249472512,
    "Ads Specialist": 1571073021630881833,
}

# Ticket departments and access/claim/close roles.
# Tickets Access is included in General Inquiry and Member Report only.
TICKET_TYPES = {
    "Creative Department Request": {
        "key": "creative",
        "ping": [1571063896977055855],
        "access": [
            1571063939213566044,  # Art Director
            1571063896977055855,  # Creative Director
            1571065144010743818,  # Game Design Lead
        ],
        "claim": [
            1571063939213566044,
            1571063896977055855,
            1571065144010743818,
        ],
    },
    "Marketing Request": {
        "key": "marketing",
        "ping": [1571065232778989679],
        "access": [
            1571065232778989679,  # Marketing Team
            1571071998086611064,  # Social Media Manager
            1571073021630881833,  # Ads Specialist
        ],
        "claim": [
            1571065232778989679,
            1571071998086611064,
            1571073021630881833,
        ],
    },
    "Technical Support": {
        "key": "technical",
        "ping": [1571065040390586409],
        "access": [1571065040390586409],  # Technical Director only
        "claim": [1571065040390586409],
    },
    "Partnerships": {
        "key": "partnerships",
        "ping": [1571065232778989679],
        "access": [1571065232778989679],  # Marketing Team
        "claim": [1571065232778989679],
    },
    "General Inquiry": {
        "key": "general",
        "ping": [1571064979686432869],
        "access": [
            1571064979686432869,  # Community Director
            TICKETS_ACCESS_ROLE_ID,
        ],
        "claim": [1571064979686432869],
    },
    "Member Report": {
        "key": "report",
        "ping": [1571062526232236223],
        "access": [
            1571062526232236223,  # Studio Director
            1571065681670180975,  # Project Manager
            1571065103413944401,  # Executive Manager
            TICKETS_ACCESS_ROLE_ID,
        ],
        "claim": [
            1571062526232236223,
            1571065681670180975,
            1571065103413944401,
        ],
    },
}

DATA_FILE = "bloxberry_data.json"
COLOR = 0xE91EAD
MAIN_PANEL_MARKER = "BloxBerry Labs • Main Recruitment"
SKILL_PANEL_MARKER = "BloxBerry Labs • Skill Recruitment"
TICKET_PANEL_MARKER = "BloxBerry Labs • Ticket Panel"

data_lock = asyncio.Lock()
seat_locks = {name: asyncio.Lock() for name in MAIN_ROLES}


def load_data():
    if not os.path.exists(DATA_FILE):
        return {"applications": {}, "tickets": {}}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            obj = json.load(f)
        if not isinstance(obj, dict):
            raise ValueError("Invalid state file")
        obj.setdefault("applications", {})
        obj.setdefault("tickets", {})
        return obj
    except (OSError, json.JSONDecodeError, ValueError):
        return {"applications": {}, "tickets": {}}


def save_data(data):
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, DATA_FILE)


def member_has_any(member, role_ids):
    ids = {r.id for r in member.roles}
    return bool(ids.intersection(role_ids))


def is_recruitment_reviewer(member):
    return member.guild_permissions.administrator or member_has_any(
        member, RECRUITMENT_REVIEWER_ROLE_IDS
    )


def is_ticket_claim_authorized(member, ticket_type):
    cfg = TICKET_TYPES.get(ticket_type)
    return bool(cfg and (
        member.guild_permissions.administrator
        or member_has_any(member, cfg["claim"])
    ))


def role_count(guild, role_id):
    role = guild.get_role(role_id)
    if role is None:
        return 0
    return sum(1 for m in guild.members if role in m.roles)


def seat_description(guild):
    lines = []
    for name, rid in MAIN_ROLES.items():
        count = role_count(guild, rid)
        limit = SEAT_LIMITS[name]
        if count >= limit:
            lines.append(f"~~{name} — {count}/{limit}~~ ❌ **FULL**")
        else:
            lines.append(f"**{name}** — `{count}/{limit}` seats filled 🟢")
    return "\n".join(lines)


def main_recruitment_embed(guild):
    e = discord.Embed(
        title="🍓 WANT TO JOIN BLOXBERRY LABS?",
        description=(
            "# Become Part of Our Team\n"
            "We're looking for talented, creative and motivated people to help us build something amazing.\n\n"
            "**WHY JOIN US?**\n"
            "• Work with a creative team.\n• Collaborate on Roblox projects.\n"
            "• Improve your skills through real projects.\n• Bring your ideas to life.\n\n"
            "---\n\n# AVAILABLE MAIN POSITIONS\n"
            f"{seat_description(guild)}\n\n---\n\n"
            "**HOW TO APPLY**\n"
            "1. Click **Apply for a Main Role**.\n"
            "2. Select your desired position.\n"
            "3. Complete the application form.\n"
            "4. Management will review your application.\n\n"
            "Want skill-based roles instead? Use the separate Skill Roles panel.\n\n"
            "*Your skills matter. Your contribution makes the difference.*\n"
            "**BloxBerry Labs** 🍓"
        ),
        color=COLOR,
    )
    if guild.icon:
        e.set_thumbnail(url=guild.icon.url)
    e.set_footer(text=MAIN_PANEL_MARKER)
    return e


def skill_recruitment_embed(guild):
    role_lines = "\n".join(f"• <@&{rid}>" for rid in SKILL_ROLES.values())
    e = discord.Embed(
        title="🍓 BLOXBERRY LABS | SKILL-BASED RECRUITMENT",
        description=(
            "# Join Through Your Skills!\n"
            "Not interested in one of the main positions? Apply for skill-based roles instead.\n\n"
            "**AVAILABLE SKILL ROLES**\n"
            f"{role_lines}\n\n---\n\n"
            "**HOW TO APPLY**\n"
            "1. Select one or multiple roles from the dropdown.\n"
            "2. Click **Continue to Application Form**.\n"
            "3. Complete the application form.\n"
            "4. Management will review all selected roles.\n\n"
            "**IMPORTANT**\n"
            "• You can select multiple skill roles in one application.\n"
            "• Every selected role will appear as a Discord role mention in your application.\n"
            "• You do not need to apply for a main position.\n"
            "• Selection does not guarantee acceptance.\n\n"
            "*Show us what you can bring to the team.*\n**BloxBerry Labs** 🍓"
        ),
        color=COLOR,
    )
    if guild.icon:
        e.set_thumbnail(url=guild.icon.url)
    e.set_footer(text=SKILL_PANEL_MARKER)
    return e


def ticket_panel_embed():
    e = discord.Embed(
        title="🎟️ BLOXBERRY LABS | SUPPORT CENTER",
        description=(
            "Need help or want to contact a department? Open a private ticket below.\n\n"
            "**Available departments**\n"
            "🎨 Creative Department Request\n"
            "📢 Marketing Request\n"
            "🛠️ Technical Support\n"
            "🤝 Partnerships\n"
            "💬 General Inquiry\n"
            "🚨 Member Report\n\n"
            "Choose the category that best matches your request. Please do not open multiple tickets for the same issue."
        ),
        color=COLOR,
    )
    e.set_footer(text=TICKET_PANEL_MARKER)
    return e


def truncate(text, limit=1024):
    text = text or "—"
    return text if len(text) <= limit else text[: limit - 3] + "..."


async def send_user_dm(user, *, content=None, embed=None, reason="notification"):
    """Send a DM and return (success, readable_status); never silently hide failures."""
    try:
        dm = user.dm_channel or await user.create_dm()
        await dm.send(content=content, embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return True, "DM sent successfully."
    except discord.Forbidden:
        return False, "DM blocked by the user's privacy settings or server DM settings."
    except discord.NotFound:
        return False, "DM failed: user or DM channel was not found."
    except discord.HTTPException as exc:
        return False, f"DM failed ({exc.status}): {exc.text or str(exc)}"
    except Exception as exc:
        return False, f"DM failed ({type(exc).__name__}): {exc}"


# ============================================================
# RECRUITMENT MODAL
# ============================================================

class RecruitmentModal(discord.ui.Modal):
    def __init__(self, app_type, selected_names):
        super().__init__(
            title="Main Role Application" if app_type == "main" else "Skill Role Application",
            timeout=300,
        )
        self.app_type = app_type
        self.selected_names = selected_names

        self.experience = discord.ui.TextInput(
            label="Previous Experience",
            placeholder="Tell us about your relevant experience...",
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        self.skills = discord.ui.TextInput(
            label="Your Skills",
            placeholder="What skills make you suitable for these roles?",
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        self.portfolio = discord.ui.TextInput(
            label="Portfolio / Previous Work",
            placeholder="Share links or write None.",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=False,
        )
        self.availability = discord.ui.TextInput(
            label="Availability",
            placeholder="How many hours can you contribute weekly?",
            max_length=300,
        )
        self.reason = discord.ui.TextInput(
            label="Why BloxBerry Labs?",
            placeholder="Why do you want to join our team?",
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        for item in (
            self.experience, self.skills, self.portfolio,
            self.availability, self.reason
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        user_key = str(interaction.user.id)

        async with data_lock:
            existing = interaction.client.state["applications"].get(user_key)
            if existing and existing.get("status") == "pending":
                await interaction.followup.send(
                    "You already have a pending recruitment application.",
                    ephemeral=True,
                )
                return

            guild = interaction.guild
            role_map = MAIN_ROLES if self.app_type == "main" else SKILL_ROLES
            role_ids = [role_map[n] for n in self.selected_names]

            for rid in role_ids:
                if guild.get_role(rid) is None:
                    await interaction.followup.send(
                        f"One of the requested roles (`{rid}`) no longer exists.",
                        ephemeral=True,
                    )
                    return

            if self.app_type == "main":
                name = self.selected_names[0]
                async with seat_locks[name]:
                    if role_count(guild, role_ids[0]) >= SEAT_LIMITS[name]:
                        await interaction.followup.send(
                            "Sorry, this position has just been filled.",
                            ephemeral=True,
                        )
                        await interaction.client.refresh_main_panel()
                        return

            pending_role = guild.get_role(PENDING_APPLICATION_ROLE_ID)
            try:
                if pending_role and pending_role not in interaction.user.roles:
                    await interaction.user.add_roles(
                        pending_role, reason="Recruitment application submitted"
                    )
            except discord.HTTPException as exc:
                await interaction.followup.send(
                    f"Could not assign the pending-application role: {exc}",
                    ephemeral=True,
                )
                return

            review_channel = interaction.client.get_channel(APPLICATION_REVIEW_CHANNEL_ID)
            if review_channel is None:
                review_channel = await interaction.client.fetch_channel(APPLICATION_REVIEW_CHANNEL_ID)

            mentions = "\n".join(f"• <@&{rid}>" for rid in role_ids)
            type_label = "Main Position" if self.app_type == "main" else "Skill-Based Roles"
            embed = discord.Embed(
                title="📋 NEW BLOXBERRY LABS APPLICATION",
                description=(
                    f"**Applicant:** {interaction.user.mention}\n"
                    f"**Application Type:** {type_label}\n"
                    f"**Requested Role(s):**\n{mentions}\n\n"
                    "**Status:** 🟡 PENDING REVIEW"
                ),
                color=COLOR,
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Previous Experience", value=truncate(self.experience.value), inline=False)
            embed.add_field(name="Skills", value=truncate(self.skills.value), inline=False)
            embed.add_field(name="Portfolio / Previous Work", value=truncate(self.portfolio.value or "Not provided"), inline=False)
            embed.add_field(name="Availability", value=truncate(self.availability.value), inline=False)
            embed.add_field(name="Why BloxBerry Labs?", value=truncate(self.reason.value), inline=False)
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.set_footer(text=f"Recruitment Application ID: {interaction.user.id}")

            review_message = await review_channel.send(
                embed=embed, view=ApplicationReviewView()
            )
            interaction.client.state["applications"][user_key] = {
                "status": "pending",
                "type": self.app_type,
                "role_ids": role_ids,
                "role_names": self.selected_names,
                "review_message_id": review_message.id,
                "review_channel_id": APPLICATION_REVIEW_CHANNEL_ID,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            interaction.client.save_state()

        await interaction.followup.send(
            "✅ Your application has been submitted!\n\n"
            f"**Requested role(s):**\n{mentions}\n\n"
            "Management will review your application.",
            ephemeral=True,
        )


# ============================================================
# RECRUITMENT SELECTS / VIEWS
# ============================================================

class MainRoleSelect(discord.ui.Select):
    def __init__(self, guild):
        options = []
        for name, rid in MAIN_ROLES.items():
            count = role_count(guild, rid)
            if count < SEAT_LIMITS[name]:
                options.append(discord.SelectOption(
                    label=name, value=name,
                    description=f"{SEAT_LIMITS[name] - count} seat(s) remaining",
                ))
        if not options:
            options = [discord.SelectOption(label="All positions are full", value="none")]
        super().__init__(
            placeholder="Select your main position...",
            min_values=1, max_values=1, options=options,
        )

    async def callback(self, interaction):
        name = self.values[0]
        if name == "none":
            await interaction.response.send_message("All main positions are full.", ephemeral=True)
            return
        if interaction.client.has_pending_application(interaction.user.id):
            await interaction.response.send_message("You already have a pending application.", ephemeral=True)
            return
        await interaction.response.send_modal(RecruitmentModal("main", [name]))


class MainRoleView(discord.ui.View):
    def __init__(self, guild):
        super().__init__(timeout=180)
        self.add_item(MainRoleSelect(guild))


class MainApplyButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Apply for a Main Role", emoji="🍓",
            style=discord.ButtonStyle.primary, custom_id="bb_all_main_apply",
        )

    async def callback(self, interaction):
        if interaction.client.has_pending_application(interaction.user.id):
            await interaction.response.send_message("You already have a pending application.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Choose the main position you want to apply for:",
            view=MainRoleView(interaction.guild), ephemeral=True,
        )


class MainPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(MainApplyButton())


class SkillRoleSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=name, value=name, description=f"Apply for {name}")
            for name in SKILL_ROLES
        ]
        super().__init__(
            placeholder="Select one or multiple skill roles...",
            min_values=1, max_values=len(options), options=options,
            custom_id="bb_all_skill_select",
        )

    async def callback(self, interaction):
        if interaction.client.has_pending_application(interaction.user.id):
            await interaction.response.send_message(
                "You already have a pending application. Wait for management to review it.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Selected roles:\n" + "\n".join(f"• **{n}**" for n in self.values)
            + "\n\nClick below to open the application form.",
            view=SkillContinueView(list(self.values)),
            ephemeral=True,
        )


class SkillContinueButton(discord.ui.Button):
    def __init__(self, selected_names):
        super().__init__(label="Continue to Application Form", emoji="📝", style=discord.ButtonStyle.success)
        self.selected_names = selected_names

    async def callback(self, interaction):
        if interaction.client.has_pending_application(interaction.user.id):
            await interaction.response.send_message("You already have a pending application.", ephemeral=True)
            return
        await interaction.response.send_modal(RecruitmentModal("skill", self.selected_names))


class SkillContinueView(discord.ui.View):
    def __init__(self, selected_names):
        super().__init__(timeout=180)
        self.add_item(SkillContinueButton(selected_names))


class SkillPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(SkillRoleSelect())


# ============================================================
# RECRUITMENT REVIEW
# ============================================================

class AcceptApplicationButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Accept", emoji="✅", style=discord.ButtonStyle.success,
            custom_id="bb_all_application_accept",
        )

    async def callback(self, interaction):
        await finalize_application(interaction, "accept")


class DenyApplicationButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Deny", emoji="❌", style=discord.ButtonStyle.danger,
            custom_id="bb_all_application_deny",
        )

    async def callback(self, interaction):
        await finalize_application(interaction, "deny")


class ApplicationReviewView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(AcceptApplicationButton())
        self.add_item(DenyApplicationButton())


class ApplicationReviewedView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Application Reviewed", emoji="🔒",
            style=discord.ButtonStyle.secondary, disabled=True,
        ))


def application_id_from_message(message):
    if not message.embeds:
        return None
    footer = message.embeds[0].footer.text or ""
    prefix = "Recruitment Application ID:"
    if not footer.startswith(prefix):
        return None
    try:
        return int(footer.split(":", 1)[1].strip())
    except ValueError:
        return None


async def finalize_application(interaction, decision):
    if not is_recruitment_reviewer(interaction.user):
        await interaction.response.send_message(
            "❌ You are not authorized to review applications.", ephemeral=True
        )
        return

    applicant_id = application_id_from_message(interaction.message)
    if applicant_id is None:
        await interaction.response.send_message("Could not identify this application.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    key = str(applicant_id)

    async with data_lock:
        record = interaction.client.state["applications"].get(key)
        if (
            not record or record.get("status") != "pending"
            or record.get("review_message_id") != interaction.message.id
        ):
            await interaction.followup.send("This application has already been reviewed.", ephemeral=True)
            return

        guild = interaction.guild
        try:
            applicant = guild.get_member(applicant_id) or await guild.fetch_member(applicant_id)
        except discord.NotFound:
            await interaction.followup.send("Applicant is no longer in the server.", ephemeral=True)
            return

        role_ids = [int(x) for x in record["role_ids"]]
        roles = [guild.get_role(rid) for rid in role_ids]
        if any(r is None for r in roles):
            await interaction.followup.send("One or more requested roles no longer exist. Application remains pending.", ephemeral=True)
            return

        if decision == "accept":
            if record["type"] == "main":
                name = record["role_names"][0]
                async with seat_locks[name]:
                    role = roles[0]
                    if role not in applicant.roles and role_count(guild, role.id) >= SEAT_LIMITS[name]:
                        await interaction.followup.send(
                            "All seats for this position are full. Application remains pending.",
                            ephemeral=True,
                        )
                        return
                    if role not in applicant.roles:
                        await applicant.add_roles(role, reason=f"Recruitment accepted by {interaction.user}")
            else:
                missing = [r for r in roles if r not in applicant.roles]
                if missing:
                    await applicant.add_roles(*missing, reason=f"Skill recruitment accepted by {interaction.user}")

        pending_role = guild.get_role(PENDING_APPLICATION_ROLE_ID)
        if pending_role and pending_role in applicant.roles:
            await applicant.remove_roles(pending_role, reason=f"Recruitment application {decision}")

        record["status"] = decision
        record["reviewed_by"] = interaction.user.id
        record["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        interaction.client.save_state()

    # Staff review embeds keep role mentions; applicant DMs use plain role names.
    mentions = "\\n".join(f"• <@&{rid}>" for rid in role_ids)
    role_names_text = "\\n".join(f"• **{name}**" for name in record.get("role_names", []))
    type_label = "Main Position" if record["type"] == "main" else "Skill-Based Roles"

    if decision == "accept":
        status = "🟢 ACCEPTED"
        color = discord.Color.green()
        dm_title = "🎉 Your BloxBerry Labs Application Was Accepted!"
        dm_body = (
            "Congratulations! Your application has been accepted.\\n\\n"
            f"**Application type:** {type_label}\\n"
            f"**Approved role(s):**\\n{role_names_text}\\n\\n"
            "Welcome to BloxBerry Labs! 🍓"
        )
    else:
        status = "🔴 DENIED"
        color = discord.Color.red()
        dm_title = "BloxBerry Labs Application Update"
        dm_body = (
            "Thank you for applying to BloxBerry Labs.\\n\\n"
            f"**Application type:** {type_label}\\n"
            f"**Role(s) applied for:**\\n{role_names_text}\\n\\n"
            "Unfortunately, your application was not accepted this time.\\n\\n"
            "We appreciate your interest in our team."
        )

    e = interaction.message.embeds[0]
    e.color = color
    e.description = (
        f"**Applicant:** <@{applicant_id}>\n"
        f"**Application Type:** {type_label}\n"
        f"**Requested Role(s):**\n{mentions}\n\n"
        f"**Decision:** {status}\n**Reviewed By:** {interaction.user.mention}"
    )
    await interaction.message.edit(embed=e, view=ApplicationReviewedView())
    await interaction.client.refresh_main_panel()

    dm_ok, dm_note = await send_user_dm(
        applicant,
        embed=discord.Embed(title=dm_title, description=dm_body, color=COLOR),
        reason="application decision",
    )
    if not dm_ok:
        print(f"Application DM to {applicant_id} failed: {dm_note}", flush=True)

    await interaction.followup.send(f"Decision completed: **{status}**\n{dm_note}", ephemeral=True)


# ============================================================
# TICKET SYSTEM
# ============================================================

def ticket_channel_name(ticket_type, username):
    cfg = TICKET_TYPES[ticket_type]
    slug = re.sub(r"[^a-z0-9]+", "-", cfg["key"].lower()).strip("-")
    user = re.sub(r"[^a-z0-9]+", "-", username.lower()).strip("-")[:18] or "member"
    return f"{slug}-{user}"[:95]


def ticket_record_for_channel(client, channel_id):
    return client.state["tickets"].get(str(channel_id))


def ticket_staff_can_manage(member, record):
    return (
        member.guild_permissions.administrator
        or is_ticket_claim_authorized(member, record["ticket_type"])
    )


class TicketTypeSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=name, value=name, description=f"Open a {name.lower()} ticket")
            for name in TICKET_TYPES
        ]
        super().__init__(
            placeholder="Choose a ticket category...",
            min_values=1, max_values=1, options=options,
            custom_id="bb_all_ticket_type",
        )

    async def callback(self, interaction):
        ticket_type = self.values[0]
        await create_ticket(interaction, ticket_type)


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect())


async def create_ticket(interaction, ticket_type):
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild = interaction.guild
    cfg = TICKET_TYPES[ticket_type]

    # Prevent duplicate open tickets of the same type for the same user.
    for channel_id, record in interaction.client.state["tickets"].items():
        if (
            record.get("owner_id") == interaction.user.id
            and record.get("ticket_type") == ticket_type
            and record.get("status") == "open"
        ):
            existing = guild.get_channel(int(channel_id))
            if existing:
                await interaction.followup.send(
                    f"You already have an open ticket: {existing.mention}",
                    ephemeral=True,
                )
                return

    category = guild.get_channel(TICKET_CATEGORY_ID)
    if category is None:
        try:
            category = await guild.fetch_channel(TICKET_CATEGORY_ID)
        except discord.HTTPException:
            await interaction.followup.send("Ticket category was not found.", ephemeral=True)
            return

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, embed_links=True,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            manage_channels=True, manage_messages=True, attach_files=True,
        ),
    }

    # Tickets Access role has visibility in every ticket for assistance,
    # but does not automatically get claim permissions.
    tickets_access = guild.get_role(TICKETS_ACCESS_ROLE_ID)
    if tickets_access:
        overwrites[tickets_access] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True,
        )

    for rid in cfg["access"]:
        role = guild.get_role(rid)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                attach_files=True, embed_links=True,
            )

    channel = await guild.create_text_channel(
        name=ticket_channel_name(ticket_type, interaction.user.name),
        category=category,
        overwrites=overwrites,
        topic=f"BloxBerry ticket | Owner ID: {interaction.user.id} | Type: {ticket_type}",
        reason=f"Ticket opened by {interaction.user}",
    )

    record = {
        "owner_id": interaction.user.id,
        "ticket_type": ticket_type,
        "status": "open",
        "claimed_by": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    interaction.client.state["tickets"][str(channel.id)] = record
    interaction.client.save_state()

    role_pings = " ".join(f"<@&{rid}>" for rid in cfg["ping"])
    e = discord.Embed(
        title=f"🎟️ {ticket_type}",
        description=(
            f"Welcome {interaction.user.mention}!\n\n"
            "Please describe your request with as much detail as possible. "
            "A member of the appropriate department will assist you.\n\n"
            f"**Ticket owner:** {interaction.user.mention}\n"
            f"**Department:** {ticket_type}\n"
            "**Status:** 🟢 Open"
        ),
        color=COLOR,
        timestamp=discord.utils.utcnow(),
    )
    e.set_footer(text=f"Ticket Owner ID: {interaction.user.id} • Ticket Channel ID: {channel.id}")

    await channel.send(
        content=f"{interaction.user.mention} {role_pings}",
        embed=e,
        view=TicketControlView(),
        allowed_mentions=discord.AllowedMentions(users=True, roles=True),
    )

    dm_ok, dm_note = await send_user_dm(
        interaction.user,
        embed=discord.Embed(
            title="🎟️ Your BloxBerry Labs ticket is open",
            description=(
                f"Your **{ticket_type}** ticket has been created in **{interaction.guild.name}**.\n\n"
                f"**Ticket:** #{channel.name}\n"
                f"[Open your ticket](https://discord.com/channels/{interaction.guild.id}/{channel.id})"
            ),
            color=COLOR,
        ),
        reason="ticket created",
    )
    if not dm_ok:
        print(f"Ticket creation DM to {interaction.user.id} failed: {dm_note}", flush=True)

    await interaction.followup.send(
        f"✅ Your ticket has been created: {channel.mention}\n"
        + ("📩 I also sent you a DM." if dm_ok else f"⚠️ I couldn't DM you: {dm_note}"),
        ephemeral=True,
    )


class ClaimButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Claim", emoji="🙋", style=discord.ButtonStyle.primary,
            custom_id="bb_all_ticket_claim",
        )

    async def callback(self, interaction):
        record = ticket_record_for_channel(interaction.client, interaction.channel.id)
        if not record or record.get("status") != "open":
            await interaction.response.send_message("This ticket is not open.", ephemeral=True)
            return
        if not is_ticket_claim_authorized(interaction.user, record["ticket_type"]):
            await interaction.response.send_message(
                "You are not authorized to claim this department's tickets.",
                ephemeral=True,
            )
            return
        if record.get("claimed_by"):
            await interaction.response.send_message(
                f"This ticket is already claimed by <@{record['claimed_by']}>.",
                ephemeral=True,
            )
            return

        record["claimed_by"] = interaction.user.id
        interaction.client.save_state()

        e = discord.Embed(
            title="🙋 Ticket Claimed",
            description=f"This ticket has been claimed by {interaction.user.mention}.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        await interaction.response.send_message(embed=e)
        await interaction.channel.send(
            f"{interaction.user.mention} is now handling this ticket."
        )


class UnclaimButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Unclaim", emoji="↩️", style=discord.ButtonStyle.secondary,
            custom_id="bb_all_ticket_unclaim",
        )

    async def callback(self, interaction):
        record = ticket_record_for_channel(interaction.client, interaction.channel.id)
        if not record or record.get("status") != "open":
            await interaction.response.send_message("This ticket is not open.", ephemeral=True)
            return
        claimant = record.get("claimed_by")
        if claimant is None:
            await interaction.response.send_message("This ticket is not currently claimed.", ephemeral=True)
            return
        if interaction.user.id != claimant and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only the claimant or an administrator can unclaim this ticket.", ephemeral=True)
            return

        record["claimed_by"] = None
        interaction.client.save_state()
        e = discord.Embed(
            title="↩️ Ticket Unclaimed",
            description=f"{interaction.user.mention} unclaimed this ticket.",
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=e)


class CloseButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Close Ticket", emoji="🔒", style=discord.ButtonStyle.danger,
            custom_id="bb_all_ticket_close",
        )

    async def callback(self, interaction):
        record = ticket_record_for_channel(interaction.client, interaction.channel.id)
        if not record or record.get("status") != "open":
            await interaction.response.send_message("This ticket is already closed or is not recognized.", ephemeral=True)
            return

        owner = record["owner_id"]
        is_owner = interaction.user.id == owner
        is_staff = ticket_staff_can_manage(interaction.user, record)
        if not (is_owner or is_staff):
            await interaction.response.send_message("Only the ticket owner or authorized department staff can close this ticket.", ephemeral=True)
            return

        await interaction.response.send_message(
            "Are you sure you want to close this ticket? The transcript will be saved and this channel will be deleted.",
            view=CloseConfirmView(),
            ephemeral=True,
        )


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ClaimButton())
        self.add_item(UnclaimButton())
        self.add_item(CloseButton())


class CloseConfirmButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Confirm Close", emoji="🗑️", style=discord.ButtonStyle.danger)

    async def callback(self, interaction):
        await close_ticket(interaction)


class CloseCancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Cancel", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction):
        await interaction.response.edit_message(content="Ticket closure cancelled.", view=None)


class CloseConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(CloseConfirmButton())
        self.add_item(CloseCancelButton())


async def build_transcript(channel):
    lines = [
        f"BloxBerry Labs Ticket Transcript",
        f"Channel: #{channel.name} ({channel.id})",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "=" * 70,
    ]
    async for msg in channel.history(limit=None, oldest_first=True):
        timestamp = msg.created_at.isoformat()
        author = f"{msg.author} ({msg.author.id})"
        content = msg.clean_content or ""
        if msg.attachments:
            content += " | Attachments: " + ", ".join(a.url for a in msg.attachments)
        if msg.embeds:
            for emb in msg.embeds:
                if emb.title:
                    content += f" | Embed title: {emb.title}"
                if emb.description:
                    content += f" | Embed: {emb.description}"
        lines.append(f"[{timestamp}] {author}: {content}")
    return "\n".join(lines)


async def close_ticket(interaction):
    record = ticket_record_for_channel(interaction.client, interaction.channel.id)
    if not record or record.get("status") != "open":
        await interaction.response.send_message("Ticket is already closed.", ephemeral=True)
        return

    owner_id = record["owner_id"]
    if not (
        interaction.user.id == owner_id
        or ticket_staff_can_manage(interaction.user, record)
    ):
        await interaction.response.send_message("You cannot close this ticket.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    channel = interaction.channel
    guild = interaction.guild

    try:
        transcript_text = await build_transcript(channel)
        transcript_file = discord.File(
            io.BytesIO(transcript_text.encode("utf-8")),
            filename=f"{channel.name}-transcript.txt",
        )
        transcript_channel = interaction.client.get_channel(TRANSCRIPT_CHANNEL_ID)
        if transcript_channel is None:
            transcript_channel = await interaction.client.fetch_channel(TRANSCRIPT_CHANNEL_ID)

        transcript_embed = discord.Embed(
            title="📄 Ticket Transcript",
            description=(
                f"**Ticket:** `{channel.name}`\n"
                f"**Ticket ID:** `{channel.id}`\n"
                f"**Owner:** <@{owner_id}>\n"
                f"**Type:** {record['ticket_type']}\n"
                f"**Closed by:** {interaction.user.mention}"
            ),
            color=COLOR,
            timestamp=discord.utils.utcnow(),
        )
        await transcript_channel.send(embed=transcript_embed, file=transcript_file)
    except Exception as exc:
        await interaction.followup.send(
            f"Could not save transcript, so the ticket was not deleted: {exc}",
            ephemeral=True,
        )
        return

    record["status"] = "closed"
    record["closed_by"] = interaction.user.id
    record["closed_at"] = datetime.now(timezone.utc).isoformat()
    interaction.client.save_state()

    owner = guild.get_member(owner_id)
    if owner is None:
        try:
            owner = await guild.fetch_member(owner_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            owner = None
    if owner is not None:
        dm_ok, dm_note = await send_user_dm(
            owner,
            embed=discord.Embed(
                title="🔒 Your BloxBerry Labs ticket was closed",
                description=(
                    f"Your **{record['ticket_type']}** ticket has been closed.\n"
                    "A transcript has been saved by the support team."
                ),
                color=COLOR,
            ),
            reason="ticket closed",
        )
        if not dm_ok:
            print(f"Ticket-close DM to {owner_id} failed: {dm_note}", flush=True)
    else:
        dm_ok, dm_note = False, "Ticket owner is no longer in the server."

    await interaction.followup.send(
        "Transcript saved. " + ("Owner notified by DM. " if dm_ok else f"Owner DM failed: {dm_note} ")
        + "This ticket channel will be deleted in 5 seconds.",
        ephemeral=True,
    )
    await channel.send(
        f"🔒 Ticket closed by {interaction.user.mention}. Transcript saved. Deleting this channel in 5 seconds."
    )
    await asyncio.sleep(5)
    try:
        await channel.delete(reason=f"Ticket closed by {interaction.user}")
    except discord.HTTPException as exc:
        print(f"Could not delete ticket channel {channel.id}: {exc}", flush=True)


# ============================================================
# /ADD USER COMMAND
# ============================================================

@app_commands.guild_only()
class TicketCommands(app_commands.Group):
    def __init__(self):
        super().__init__(name="ticket", description="Ticket management commands")

    @app_commands.command(name="add", description="Add a member to the current ticket")
    @app_commands.describe(user="Member to add to this ticket")
    async def add(self, interaction: discord.Interaction, user: discord.Member):
        record = ticket_record_for_channel(interaction.client, interaction.channel.id)
        if not record or record.get("status") != "open":
            await interaction.response.send_message("Use this command inside an open ticket.", ephemeral=True)
            return

        authorized = (
            interaction.user.guild_permissions.administrator
            or interaction.user.id == record.get("claimed_by")
            or is_ticket_claim_authorized(interaction.user, record["ticket_type"])
        )
        if not authorized:
            await interaction.response.send_message(
                "Only authorized department staff, the claimant, or an administrator can add users.",
                ephemeral=True,
            )
            return

        if user.bot:
            await interaction.response.send_message("You cannot add a bot to a ticket.", ephemeral=True)
            return

        await interaction.channel.set_permissions(
            user,
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
            reason=f"Added by {interaction.user}",
        )

        audit = discord.Embed(
            title="➕ Member Added to Ticket",
            description=(
                f"**Added member:** {user.mention}\n"
                f"**Added by:** {interaction.user.mention}\n"
                f"**Ticket:** {interaction.channel.mention}"
            ),
            color=COLOR,
            timestamp=discord.utils.utcnow(),
        )
        await interaction.response.send_message(embed=audit)
        dm_ok, dm_note = await send_user_dm(
            user,
            embed=discord.Embed(
                title="🎟️ You were added to a BloxBerry Labs ticket",
                description=(
                    f"You were added to **{interaction.guild.name}** ticket **#{interaction.channel.name}** "
                    f"by **{interaction.user.display_name}**.\n\n"
                    f"[Open ticket](https://discord.com/channels/{interaction.guild.id}/{interaction.channel.id})"
                ),
                color=COLOR,
            ),
            reason="added to ticket",
        )
        if not dm_ok:
            print(f"Ticket-add DM to {user.id} failed: {dm_note}", flush=True)
            await interaction.followup.send(f"⚠️ User was added, but their DM failed: {dm_note}", ephemeral=True)
        else:
            await interaction.followup.send("📩 The added member was notified by DM.", ephemeral=True)


# ============================================================
# PURGE - DELETE A SPECIFIED NUMBER OF MESSAGES IN THE CURRENT CHANNEL
# Usage: purge 10  -> deletes the 10 messages immediately before the command,
#                    then deletes the purge command itself.
# ============================================================

async def purge_channel_messages(message, amount):
    if not message.guild or not any(r.id == PURGE_AUTH_ROLE_ID for r in message.author.roles):
        await message.reply("You are not authorized to use purge.", delete_after=5)
        return

    if not message.channel.permissions_for(message.guild.me).manage_messages:
        await message.reply("I need the **Manage Messages** permission in this channel.", delete_after=7)
        return

    # Collect only the requested number of messages before the command.
    # The command itself is removed separately and is not counted in `amount`.
    targets = []
    try:
        async for msg in message.channel.history(limit=amount + 1, oldest_first=False):
            if msg.id == message.id:
                continue
            targets.append(msg)
            if len(targets) >= amount:
                break
    except discord.Forbidden:
        await message.reply("I need **View Channel** and **Read Message History** permissions.", delete_after=7)
        return
    except discord.HTTPException as exc:
        await message.reply(f"Couldn't read channel history: {exc}", delete_after=7)
        return

    deleted_count = 0
    # Bulk delete messages younger than 14 days; delete older messages individually.
    cutoff = discord.utils.utcnow() - timedelta(days=14)
    recent = [m for m in targets if m.created_at > cutoff]
    old = [m for m in targets if m.created_at <= cutoff]

    try:
        for i in range(0, len(recent), 100):
            batch = recent[i:i + 100]
            if len(batch) == 1:
                await batch[0].delete()
                deleted_count += 1
            elif batch:
                await message.channel.delete_messages(batch, reason=f"Purge requested by {message.author}")
                deleted_count += len(batch)

        for msg in old:
            try:
                await msg.delete(reason=f"Purge requested by {message.author}")
                deleted_count += 1
            except discord.NotFound:
                pass

        # Always remove the command so the channel stays clean.
        try:
            await message.delete()
        except discord.NotFound:
            pass

        print(
            f"Purge completed in #{message.channel.name}; "
            f"deleted {deleted_count} requested messages plus the command.",
            flush=True,
        )
    except discord.Forbidden as exc:
        print(f"Purge permission error in channel {message.channel.id}: {exc}", flush=True)
        try:
            await message.channel.send("I couldn't finish the purge. Check my **Manage Messages** permission.", delete_after=7)
        except discord.HTTPException:
            pass
    except discord.HTTPException as exc:
        print(f"Purge failed in channel {message.channel.id}: {exc}", flush=True)
        try:
            await message.channel.send(f"Purge stopped because of a Discord error: {exc}", delete_after=7)
        except discord.HTTPException:
            pass


# ============================================================
# BOT
# ============================================================

class BloxBerryBot(discord.Client):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.state = load_data()
        self.main_panel_message = None
        self.skill_panel_message = None
        self.tree = app_commands.CommandTree(self)
        self.tree.add_command(TicketCommands())

    def save_state(self):
        save_data(self.state)

    def has_pending_application(self, user_id):
        record = self.state["applications"].get(str(user_id))
        return bool(record and record.get("status") == "pending")

    async def setup_hook(self):
        self.add_view(MainPanelView())
        self.add_view(SkillPanelView())
        self.add_view(TicketPanelView())
        self.add_view(TicketControlView())
        self.add_view(ApplicationReviewView())

        try:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} slash commands.", flush=True)
        except Exception as exc:
            print(f"Slash command sync failed: {exc}", flush=True)

    async def refresh_main_panel(self):
        if self.main_panel_message is None:
            return
        try:
            await self.main_panel_message.edit(
                embed=main_recruitment_embed(self.main_panel_message.guild),
                view=MainPanelView(),
            )
        except discord.NotFound:
            self.main_panel_message = None
        except discord.HTTPException as exc:
            print(f"Main panel refresh failed: {exc}", flush=True)

    async def ensure_panel(self, channel_id, marker, embed, view, attr):
        channel = await self.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"Channel {channel_id} is not a text channel.")

        found = None
        async for msg in channel.history(limit=100):
            if msg.author.id == self.user.id and msg.embeds:
                if (msg.embeds[0].footer.text or "") == marker:
                    found = msg
                    break

        if found:
            await found.edit(embed=embed, view=view)
            setattr(self, attr, found)
            print(f"Updated panel in {channel_id}", flush=True)
        else:
            sent = await channel.send(embed=embed, view=view)
            setattr(self, attr, sent)
            print(f"Posted panel in {channel_id}", flush=True)

    async def on_ready(self):
        print(f"Logged in as {self.user}.", flush=True)

        # Rich Presence: show "Watching BloxBerry Labs".
        try:
            activity = discord.Activity(
                type=discord.ActivityType.watching,
                name="BloxBerry Labs",
            )
            await self.change_presence(
                activity=activity,
                status=discord.Status.online,
            )
            print('[PRESENCE] Set activity to "Watching BloxBerry Labs".', flush=True)
        except Exception as exc:
            print(f"[PRESENCE] Could not set activity: {type(exc).__name__}: {exc}", flush=True)

        print(
            f"[WELCOME] members_intent={self.intents.members}; "
            f"message_content_intent={self.intents.message_content}; "
            f"welcome_channel_id={WELCOME_CHANNEL_ID}",
            flush=True,
        )
        if getattr(self, "_startup_done", False):
            return
        self._startup_done = True

        # Existing welcome embed is deliberately NOT posted or edited.
        try:
            await self.ensure_panel(
                MAIN_RECRUITMENT_CHANNEL_ID,
                MAIN_PANEL_MARKER,
                main_recruitment_embed((await self.fetch_channel(MAIN_RECRUITMENT_CHANNEL_ID)).guild),
                MainPanelView(),
                "main_panel_message",
            )
            await self.ensure_panel(
                SKILL_RECRUITMENT_CHANNEL_ID,
                SKILL_PANEL_MARKER,
                skill_recruitment_embed((await self.fetch_channel(SKILL_RECRUITMENT_CHANNEL_ID)).guild),
                SkillPanelView(),
                "skill_panel_message",
            )
            await self.ensure_panel(
                TICKET_PANEL_CHANNEL_ID,
                TICKET_PANEL_MARKER,
                ticket_panel_embed(),
                TicketPanelView(),
                "ticket_panel_message",
            )
        except Exception as exc:
            print(f"Panel setup warning: {type(exc).__name__}: {exc}", flush=True)

        print("BloxBerry Labs all-in-one bot is ready.", flush=True)

    async def on_member_join(self, member):
        """Assign the member role and post a clean welcome with join time and member number."""
        print(
            f"[WELCOME] member_join received: {member} ({member.id}) "
            f"in {member.guild.name} ({member.guild.id})",
            flush=True,
        )

        # Give the default member role, but do not let role errors stop the welcome message.
        role = member.guild.get_role(AUTO_MEMBER_ROLE_ID)
        if role is None:
            print(f"[WELCOME] Member role {AUTO_MEMBER_ROLE_ID} was not found.", flush=True)
        else:
            try:
                await member.add_roles(role, reason="Automatic member role")
                print(f"[WELCOME] Assigned member role to {member.id}.", flush=True)
            except discord.Forbidden as exc:
                print(f"[WELCOME] Could not assign member role; check Manage Roles and role hierarchy: {exc}", flush=True)
            except discord.HTTPException as exc:
                print(f"[WELCOME] Member role assignment failed: {exc}", flush=True)

        try:
            channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
            if channel is None:
                channel = await member.guild.fetch_channel(WELCOME_CHANNEL_ID)

            if not isinstance(channel, discord.TextChannel):
                raise RuntimeError(
                    f"Welcome channel {WELCOME_CHANNEL_ID} is not a normal text channel."
                )

            bot_member = member.guild.me
            if bot_member is None:
                bot_member = await member.guild.fetch_member(self.user.id)

            perms = channel.permissions_for(bot_member)
            if not perms.view_channel or not perms.send_messages:
                raise RuntimeError(
                    f"Bot lacks channel permissions: view_channel={perms.view_channel}, "
                    f"send_messages={perms.send_messages}"
                )

            # Discord updates guild.member_count with the join event. This is the
            # member's arrival number (including the newly joined member).
            join_number = member.guild.member_count
            if not join_number:
                join_number = "—"

            joined_at = member.joined_at or discord.utils.utcnow()
            unix_joined = int(joined_at.timestamp())

            embed = discord.Embed(
                title="🍓 A new berry just joined!",
                description=(
                    f"Welcome to **BloxBerry Labs**, {member.mention}!\n"
                    "We're happy to have you here. Make yourself at home and enjoy the community."
                ),
                color=COLOR,
                timestamp=joined_at,
            )
            embed.add_field(
                name="Member",
                value=f"{member.mention}\n`{member.name}`",
                inline=True,
            )
            embed.add_field(
                name="You are member",
                value=f"**#{join_number}**",
                inline=True,
            )
            embed.add_field(
                name="Joined",
                value=f"<t:{unix_joined}:F>\n<t:{unix_joined}:R>",
                inline=False,
            )
            if member.guild.icon:
                embed.set_author(
                    name=member.guild.name,
                    icon_url=member.guild.icon.url,
                )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text="BloxBerry Labs • Community")

            if perms.embed_links:
                await channel.send(
                    content=member.mention,
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False),
                )
            else:
                await channel.send(
                    content=(
                        f"🍓 **A new berry just joined BloxBerry Labs!**\n"
                        f"Welcome, {member.mention}!\n"
                        f"**Member number:** #{join_number}\n"
                        f"**Joined:** <t:{unix_joined}:F> (<t:{unix_joined}:R>)"
                    ),
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False),
                )

            print(
                f"[WELCOME] Posted welcome for {member.id} in #{channel.name}; "
                f"member_number={join_number}; joined_at={joined_at.isoformat()}",
                flush=True,
            )
        except discord.Forbidden as exc:
            print(
                f"[WELCOME] Discord denied access to channel {WELCOME_CHANNEL_ID}. "
                f"Check View Channel, Send Messages and Embed Links permissions: {exc}",
                flush=True,
            )
        except discord.HTTPException as exc:
            print(f"[WELCOME] Discord API error: {exc}", flush=True)
        except Exception as exc:
            print(f"[WELCOME] Failed: {type(exc).__name__}: {exc}", flush=True)

    async def on_member_update(self, before, after):
        if before.roles != after.roles:
            await self.refresh_main_panel()

    async def on_member_remove(self, member):
        await self.refresh_main_panel()

    async def on_message(self, message):
        if message.author.bot:
            return
        if not message.guild:
            return

        # `purge N` removes N messages immediately before the command.
        # The command message is also deleted but does not count toward N.
        parts = message.content.strip().split()
        if parts and parts[0].casefold() == "purge":
            if len(parts) != 2 or not parts[1].isdigit():
                await message.reply("Use `purge 10` to delete the 10 messages before this command.", delete_after=7)
                return

            amount = int(parts[1])
            if amount < 1 or amount > 1000:
                await message.reply("Choose a number from **1 to 1000**.", delete_after=7)
                return

            await purge_channel_messages(message, amount)




# ============================================================
# RENDER HTTP ENDPOINT
# GET / and /health return: anshuman hi
# ============================================================

class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        # Render health checks may use HEAD instead of GET.
        body = b"anshuman hi"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self):
        body = b"anshuman hi"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[HTTP] {self.address_string()} - {format % args}", flush=True)


def start_keepalive_server():
    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), KeepAliveHandler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="render-http-keepalive",
        daemon=True,
    )
    thread.start()
    print(
        f"[HTTP] Listening on 0.0.0.0:{port}; GET / and /health return 'anshuman hi'.",
        flush=True,
    )
    return server


intents = discord.Intents.default()
intents.members = True
intents.message_content = True

if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError(
            "Missing DISCORD_TOKEN. Add your regenerated Discord bot token "
            "in Render > Environment."
        )

    keepalive_server = start_keepalive_server()

    async def run_discord_with_rate_limit_retry():
        """Keep the Render process alive if Discord temporarily blocks API login."""
        delay_seconds = 60
        while True:
            bot = BloxBerryBot(intents=intents, chunk_guilds_at_startup=False)
            try:
                print("[DISCORD] Connecting to Discord...", flush=True)
                await bot.start(TOKEN)
                # A normal return is unusual for a long-running bot; reconnect cleanly.
                delay_seconds = 60
            except discord.HTTPException as exc:
                if exc.status != 429:
                    raise

                retry_after = 0
                response = getattr(exc, "response", None)
                headers = getattr(response, "headers", {}) if response else {}
                try:
                    retry_after = float(headers.get("Retry-After", 0))
                except (TypeError, ValueError):
                    retry_after = 0

                wait_for = max(delay_seconds, retry_after)
                wait_for = min(wait_for, 900)
                print(
                    f"[DISCORD] Login returned HTTP 429 (temporary API rate-limit block). "
                    f"Keeping the Render web service alive; retrying in {int(wait_for)} seconds. "
                    "Do not repeatedly redeploy or regenerate the token just for this error.",
                    flush=True,
                )
                delay_seconds = min(max(delay_seconds * 2, 60), 900)
            finally:
                try:
                    await bot.close()
                except Exception:
                    pass

            await asyncio.sleep(wait_for if 'wait_for' in locals() else delay_seconds)

    asyncio.run(run_discord_with_rate_limit_retry())
