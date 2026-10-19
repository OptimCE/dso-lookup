# Attribution

This dataset tells which distribution system operator (DSO) serves each Belgian
municipality, for electricity and for natural gas. It is compiled by OptimCE from the
public sources credited below, which were last retrieved on 12 October 2026.

Every attribution of the dataset names its own sources, and every version is published
with the list of these sources: publisher, title, link, retrieval date and, for each
document that was downloaded, the SHA-256 fingerprint of its bytes. The sources were
joined on the municipalities' INS codes, reformatted and checked against each other.

The compiled dataset is licensed by OptimCE under the Creative Commons Attribution 4.0
International licence (CC BY 4.0), <https://creativecommons.org/licenses/by/4.0/>. To
reuse it, credit it as "belgian-dso-map, OptimCE", with the version used and its date and
a link to <https://github.com/OptimCE/belgian-dso-map>, and say whether you changed it.
The sources credited below keep their own licences and attribution requirements: keep
their credits as well.

## Statbel (Directorate-General Statistics – Statistics Belgium)

- Data: the REFNIS codes and names of the municipalities valid from 1 January 2025,
  their German names, and the table that converts postcodes to REFNIS codes, valid from
  1 January 2025.
- Licence: Creative Commons Attribution 4.0 International (CC BY 4.0),
  <https://statbel.fgov.be/en/cc-40>.
- Credit: Source: Statbel (Directorate-General Statistics – Statistics Belgium),
  modified: the data were joined with the other sources and reformatted.

## Fluvius

- Data: "Netbeheerder en gas energie-inhoud per gemeente", from the Fluvius open data
  portal, last updated on 1 April 2025.
- Licence: Open data license – Fluvius,
  <https://opendata.fluvius.be/p/licentieopendatafluvius>.
- Credit: Fluvius, "Netbeheerder en gas energie-inhoud per gemeente", last updated on
  1 April 2025. Fluvius does not guarantee that the data are complete or correct, and
  does not endorse this dataset.

## ORES, on Open Data Wallonie-Bruxelles

- Data: the numbers of electricity and natural gas access points per locality,
  published by ORES on the Open Data Wallonie-Bruxelles portal, used to check which
  municipalities ORES serves.
- Licence: Creative Commons CC0, as stated in the portal's metadata.
- Credit: ORES, on Open Data Wallonie-Bruxelles, with NGI-IGN for the geographic data.
  ORES asks to be told about reuses of its data (indicateurs@ores.be).

## Official acts, regulators and operators

The facts the dataset states (which operator is designated for, or operates, the
network of a municipality, and from which date) come from public documents. They are
cited by link; their text is not reproduced:

- the orders of the Walloon Government designating the distribution system operators,
  and the Walloon decree of 19 May 2023 on the merger of Bastogne and Bertogne, which
  sets the date of the merger at 2 December 2024, all published in the Moniteur belge
  (read on ejustice.just.fgov.be and on its copy etaamb.openjustice.be);
- the decisions of the Vlaamse Nutsregulator (formerly VREG), and Brugel's page on the
  Brussels distribution system operator;
- the opinions and decisions of the CWaPE and of Brugel;
- the CWaPE's public Power BI report "GRD par commune", consulted by hand on
  12 October 2026 to check the Walloon municipalities;
- documents published by the operators (AIEG, AIESH, REW, RESA, ORES Assets, Fluvius)
  and by the municipalities of Namur and Gesves.
