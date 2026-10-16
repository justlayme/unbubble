"""Resolve phone numbers and emails to contact names.

Two sources:
* iPhone: `HomeDomain/Library/AddressBook/AddressBook.sqlitedb` captured with the messages.
* Mac:    `~/Library/Application Support/AddressBook/**/AddressBook-v22.abcddb`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .util import address_key


@dataclass
class Contact:
    contact_id: str
    name: str


class ContactBook:
    def __init__(self) -> None:
        self._by_key: dict[str, Contact] = {}

    def __len__(self) -> int:
        return len({c.contact_id for c in self._by_key.values()})

    def add(self, contact_id: str, name: str, addresses: Iterable[str]) -> None:
        name = (name or "").strip()
        if not name:
            return
        contact = Contact(contact_id, name)
        for addr in addresses:
            key = address_key(addr)
            if key and key not in self._by_key:
                self._by_key[key] = contact

    def lookup(self, address: Optional[str]) -> Optional[Contact]:
        return self._by_key.get(address_key(address)) if address else None

    # ---- loaders ---------------------------------------------------------------------------

    @classmethod
    def from_ios(cls, path: Path) -> "ContactBook":
        book = cls()
        if not path.exists():
            return book
        con = _open_readonly(path)
        try:
            people = {
                row[0]: _compose_name(row[1], row[2], row[3], row[4])
                for row in con.execute("SELECT ROWID, First, Last, Organization, Nickname FROM ABPerson")
            }
            addresses: dict[int, list[str]] = {}
            # property 3 = phone, 4 = email
            for record_id, value in con.execute(
                "SELECT record_id, value FROM ABMultiValue WHERE property IN (3, 4) AND value IS NOT NULL"
            ):
                addresses.setdefault(record_id, []).append(value)
            for rid, name in people.items():
                book.add(f"ios:{rid}", name, addresses.get(rid, []))
        except sqlite3.DatabaseError:
            pass
        finally:
            con.close()
        return book

    @classmethod
    def from_macos(cls, root: Optional[Path] = None) -> "ContactBook":
        book = cls()
        root = root or Path.home() / "Library" / "Application Support" / "AddressBook"
        if not root.exists():
            return book
        try:
            dbs = list(root.glob("AddressBook-v22.abcddb")) + list(root.glob("Sources/*/AddressBook-v22.abcddb"))
        except PermissionError:
            return book
        for index, db in enumerate(dbs):
            try:
                con = _open_readonly(db)
            except sqlite3.DatabaseError:
                continue
            try:
                people = {
                    row[0]: _compose_name(row[1], row[2], row[3], row[4])
                    for row in con.execute(
                        "SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZORGANIZATION, ZNICKNAME FROM ZABCDRECORD"
                    )
                }
                addresses: dict[int, list[str]] = {}
                for owner, value in con.execute("SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"):
                    if value:
                        addresses.setdefault(owner, []).append(value)
                for owner, value in con.execute("SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS"):
                    if value:
                        addresses.setdefault(owner, []).append(value)
                for pk, name in people.items():
                    book.add(f"mac{index}:{pk}", name, addresses.get(pk, []))
            except sqlite3.DatabaseError:
                pass
            finally:
                con.close()
        return book


def _compose_name(first: Optional[str], last: Optional[str], org: Optional[str], nick: Optional[str]) -> str:
    full = " ".join(p for p in (first, last) if p and p.strip())
    return full or (org or "") or (nick or "")


def _open_readonly(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.text_factory = lambda b: b.decode("utf-8", errors="replace")
    return con
