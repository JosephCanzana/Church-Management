"""accounts.address: the country list and the Philippine address data.

Used by the extension form (dropdowns and validation) and by the extension
services (a second check on the server, so the rules hold even without the
browser's JavaScript).

The Philippine data is a plain JSON file, `static/data/ph_address.json`, shaped
{province: {municipality: [barangay, ...]}}. The browser loads the same file
for the cascading dropdowns, so the lists can never disagree. Source: PSGC
(Philippine Statistics Authority) via the MIT `psgc` npm package; NCR districts
are merged into "Metro Manila". Rebuild it when PSGC publishes changes.

Every comparison here is case-insensitive (through `clean_text`), because
older rows may be stored as "Santa Rosa" and new ones as "santa rosa".
"""
import json
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from core.text import clean_text

PHILIPPINES = "Philippines"

#: Address fields from the widest area to the smallest (the form's order).
HIERARCHY = ("country", "province", "municipality", "barangay")

COUNTRIES = (
    "Afghanistan",
    "Albania",
    "Algeria",
    "American Samoa",
    "Andorra",
    "Angola",
    "Anguilla",
    "Antarctica",
    "Antigua and Barbuda",
    "Argentina",
    "Armenia",
    "Aruba",
    "Australia",
    "Austria",
    "Azerbaijan",
    "Bahamas",
    "Bahrain",
    "Bangladesh",
    "Barbados",
    "Belarus",
    "Belgium",
    "Belize",
    "Benin",
    "Bermuda",
    "Bhutan",
    "Bolivia",
    "Bonaire, Sint Eustatius and Saba",
    "Bosnia and Herzegovina",
    "Botswana",
    "Bouvet Island",
    "Brazil",
    "British Indian Ocean Territory",
    "Brunei Darussalam",
    "Bulgaria",
    "Burkina Faso",
    "Burundi",
    "Cabo Verde",
    "Cambodia",
    "Cameroon",
    "Canada",
    "Cayman Islands",
    "Central African Republic",
    "Chad",
    "Chile",
    "China",
    "Christmas Island",
    "Cocos (Keeling) Islands",
    "Colombia",
    "Comoros",
    "Congo",
    "Congo, The Democratic Republic of the",
    "Cook Islands",
    "Costa Rica",
    "Croatia",
    "Cuba",
    "Curaçao",
    "Cyprus",
    "Czechia",
    "Côte d'Ivoire",
    "Denmark",
    "Djibouti",
    "Dominica",
    "Dominican Republic",
    "Ecuador",
    "Egypt",
    "El Salvador",
    "Equatorial Guinea",
    "Eritrea",
    "Estonia",
    "Eswatini",
    "Ethiopia",
    "Falkland Islands (Malvinas)",
    "Faroe Islands",
    "Fiji",
    "Finland",
    "France",
    "French Guiana",
    "French Polynesia",
    "French Southern Territories",
    "Gabon",
    "Gambia",
    "Georgia",
    "Germany",
    "Ghana",
    "Gibraltar",
    "Greece",
    "Greenland",
    "Grenada",
    "Guadeloupe",
    "Guam",
    "Guatemala",
    "Guernsey",
    "Guinea",
    "Guinea-Bissau",
    "Guyana",
    "Haiti",
    "Heard Island and McDonald Islands",
    "Holy See (Vatican City State)",
    "Honduras",
    "Hong Kong",
    "Hungary",
    "Iceland",
    "India",
    "Indonesia",
    "Iran",
    "Iraq",
    "Ireland",
    "Isle of Man",
    "Israel",
    "Italy",
    "Jamaica",
    "Japan",
    "Jersey",
    "Jordan",
    "Kazakhstan",
    "Kenya",
    "Kiribati",
    "Kuwait",
    "Kyrgyzstan",
    "Laos",
    "Latvia",
    "Lebanon",
    "Lesotho",
    "Liberia",
    "Libya",
    "Liechtenstein",
    "Lithuania",
    "Luxembourg",
    "Macao",
    "Madagascar",
    "Malawi",
    "Malaysia",
    "Maldives",
    "Mali",
    "Malta",
    "Marshall Islands",
    "Martinique",
    "Mauritania",
    "Mauritius",
    "Mayotte",
    "Mexico",
    "Micronesia, Federated States of",
    "Moldova",
    "Monaco",
    "Mongolia",
    "Montenegro",
    "Montserrat",
    "Morocco",
    "Mozambique",
    "Myanmar",
    "Namibia",
    "Nauru",
    "Nepal",
    "Netherlands",
    "New Caledonia",
    "New Zealand",
    "Nicaragua",
    "Niger",
    "Nigeria",
    "Niue",
    "Norfolk Island",
    "North Korea",
    "North Macedonia",
    "Northern Mariana Islands",
    "Norway",
    "Oman",
    "Pakistan",
    "Palau",
    "Palestine, State of",
    "Panama",
    "Papua New Guinea",
    "Paraguay",
    "Peru",
    "Philippines",
    "Pitcairn",
    "Poland",
    "Portugal",
    "Puerto Rico",
    "Qatar",
    "Romania",
    "Russian Federation",
    "Rwanda",
    "Réunion",
    "Saint Barthélemy",
    "Saint Helena, Ascension and Tristan da Cunha",
    "Saint Kitts and Nevis",
    "Saint Lucia",
    "Saint Martin (French part)",
    "Saint Pierre and Miquelon",
    "Saint Vincent and the Grenadines",
    "Samoa",
    "San Marino",
    "Sao Tome and Principe",
    "Saudi Arabia",
    "Senegal",
    "Serbia",
    "Seychelles",
    "Sierra Leone",
    "Singapore",
    "Sint Maarten (Dutch part)",
    "Slovakia",
    "Slovenia",
    "Solomon Islands",
    "Somalia",
    "South Africa",
    "South Georgia and the South Sandwich Islands",
    "South Korea",
    "South Sudan",
    "Spain",
    "Sri Lanka",
    "Sudan",
    "Suriname",
    "Svalbard and Jan Mayen",
    "Sweden",
    "Switzerland",
    "Syria",
    "Taiwan",
    "Tajikistan",
    "Tanzania",
    "Thailand",
    "Timor-Leste",
    "Togo",
    "Tokelau",
    "Tonga",
    "Trinidad and Tobago",
    "Tunisia",
    "Turkmenistan",
    "Turks and Caicos Islands",
    "Tuvalu",
    "Türkiye",
    "Uganda",
    "Ukraine",
    "United Arab Emirates",
    "United Kingdom",
    "United States",
    "United States Minor Outlying Islands",
    "Uruguay",
    "Uzbekistan",
    "Vanuatu",
    "Venezuela",
    "Vietnam",
    "Virgin Islands, British",
    "Virgin Islands, U.S.",
    "Wallis and Futuna",
    "Western Sahara",
    "Yemen",
    "Zambia",
    "Zimbabwe",
    "Åland Islands",
)


@lru_cache(maxsize=1)
def _country_lookup():
    """{'philippines': 'Philippines', ...}"""
    return {clean_text(name): name for name in COUNTRIES}


def canonical_country(value):
    """The country list's spelling of `value` ('' when it is not in the list)."""
    return _country_lookup().get(clean_text(value), "")


def is_philippines(country):
    """True when `country` is the Philippines, whatever its capitalisation."""
    return clean_text(country) == clean_text(PHILIPPINES)


@lru_cache(maxsize=1)
def _ph_index():
    """Lowercase lookup tables built once from the JSON file.

    {province: (Province Name, {municipality: (Municipality Name, {barangay, ...})})}
    """
    path = Path(settings.BASE_DIR) / "static" / "data" / "ph_address.json"
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    return {
        clean_text(prov): (prov, {
            clean_text(mun): (mun, {clean_text(b) for b in barangays})
            for mun, barangays in muns.items()
        })
        for prov, muns in raw.items()
    }


def check_address(data, existing=None):
    """Return {field: message} for address values that break the Philippine rules.

    An empty dict means the address is fine. Only checks the shape of the
    values, never whether they are filled (the form's `required` and
    `services.clean_extension_data` do that).

    - Country Philippines: province must be in the PSGC list, the municipality
      must belong to that province, the barangay to that municipality, and the
      postal code must be 4 digits.
    - Any other country: free text, nothing to check.
    - `existing` (the saved values of the extension being edited) lets an old
      value that is not in the list stay as it is until someone changes it.
    """
    errors = {}
    if not is_philippines(data.get("country")):
        return errors

    legacy = {k: clean_text(v) for k, v in (existing or {}).items()}

    def unchanged(field, value):
        return bool(value) and legacy.get(field) == value

    province = clean_text(data.get("province"))
    municipality = clean_text(data.get("municipality"))
    barangay = clean_text(data.get("barangay"))
    postal = (data.get("postal_code") or "").strip()

    index = _ph_index()
    prov = index.get(province)
    if province and prov is None:
        if not unchanged("province", province):
            errors["province"] = "Choose a province from the list."
    elif prov is not None and municipality:
        mun = prov[1].get(municipality)
        if mun is None:
            if not unchanged("municipality", municipality):
                errors["municipality"] = f"Choose a municipality of {prov[0]}."
        elif barangay and clean_text(barangay) not in mun[1]:
            if not unchanged("barangay", barangay):
                errors["barangay"] = f"Choose a barangay of {mun[0]}."

    if postal and not (postal.isdigit() and len(postal) == 4):
        if not unchanged("postal_code", clean_text(postal)):
            errors["postal_code"] = "Enter a 4-digit postal code."
    return errors
