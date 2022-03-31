# Reference http://www2.warwick.ac.uk/fac/sci/moac/people/students/peter_cock/python/genbank/
# Reference http://blog.csdn.net/y2701310012/article/details/42218255
# Here we extract all PFAM numbers and corresponding locations and locus_tags from an annotated genbank file.
# For pfam features that overlap with eachother, only the one with smallest pfumnumber will be kept. (alternatively we can choose the one with highest score (not done yet))
# however short overlap smaller than 1/2 of both pfum features will be tolerated.
# we did not consider the drection of pfam, because we want to tolerate gene inversion.

import sys
import argparse
import glob
import sqlite3
from Bio import SeqIO
from general_functions import connect_to_db, get_base_dir
import os.path
import re 
import traceback
from datetime import datetime


# Functions ====================================================================


def extract_information_from_gbk_record(rec):
    '''
    host description and contig accession are extracted; 
    pfams [(pfamnumber, start, end, locus_tag)] are extracted from the sequence and make up a gpfamsequence; 
    if two pfams overlap with eachother, and the overlap area is bigger then half of either of them, only the one with smallest pfumnumber will be kept.
    '''
    # Some PFAM_domain features don't have PFAM ID (this is actually a  bug from antismash (they use a dictionary of pfam name and pfam ID which need to be updated mannually), remember to check if this happen too often, if yes ask Kai to fix it).so we used try expect. .
    description = rec.description # =DEFINITION
    accession = rec.id # =ACCESSION
    sequence_length = len(rec.seq)

    cds_list = []
    gpfamsequence = []
    pfamstart = 0
    pfamend = 0
    pfamnumber = 10000     #just a number bigger than any real pfamnumber.
    if rec.features:
        for feature in rec.features:
            if feature.type == "CDS":
                try:
                    locus_tag = feature.qualifiers["locus_tag"][0]
                    cds_product = feature.qualifiers["product"][0]
                    translation = feature.qualifiers["translation"][0]
                    cdsstart = feature.location.nofuzzy_start
                    cdsend = feature.location.nofuzzy_end
                    cds_list.append( (accession, locus_tag, cds_product, translation, cdsstart, cdsend) )
                except:
                    continue
            elif feature.type == "PFAM_domain":
                try:
                    apfamid = feature.qualifiers["db_xref"][0]
                    apfamnumber = int(re.search(r"PF(\d{5})\D", apfamid).group(1))
                    apfam_locus_tag = feature.qualifiers["locus_tag"][0]
                    apfamsize = feature.location.nofuzzy_end - feature.location.nofuzzy_start
                    astrand = feature.strand
                    overlap = pfamend - feature.location.nofuzzy_start  #nofuzzy_start left most (minimum) value, regardless of strand
                except:
                    continue #pfamnumber can not be found
                if overlap > apfamsize/2 or overlap > (pfamend - pfamstart)/2:
                    if apfamnumber >= pfamnumber:
                        #print ("overlap found")
                        continue

                    #remove the previous pfam
                    gpfamsequence.pop()
                    #print ("overlapped pfam found and removed")

                pfamnumber = apfamnumber
                pfam_locus_tag = apfam_locus_tag
                pfamstart = feature.location.nofuzzy_start
                pfamend = feature.location.nofuzzy_end
                strand = astrand
                pfam = (pfam_locus_tag, pfamnumber, pfamstart, pfamend, strand, accession)
                gpfamsequence.append(pfam)

    #print ("in total ", len(cds_list), " CDS found")
    #print ("in total ", len(gpfamsequence), " pfams found")

    return description, accession, sequence_length, cds_list, gpfamsequence


def insert_record_to_DB(conn, description, accession, sequence_length, cds_list, gpfamsequence):
    '''
    '''
    # Determine hosttype
    if description.startswith("Streptomyces"):
        hosttype = "reference"
    else:
        hosttype = "query" 

    with conn:
        c = conn.cursor()
        # Insert host, this happens repeatedly for each contig, therefore insert or ignore
        sql = ''' INSERT OR IGNORE INTO hosts (description, hosttype) VALUES (?,?) '''
        c.execute(sql, (description, hosttype))
        # Get hostID
        sql = ''' SELECT hostID from hosts WHERE description=? '''
        hostID = c.execute(sql, (description,)).fetchall()[0][0]
        # Insert contig
        sql = ''' INSERT INTO contigs (contig, hostID, sequence_length) VALUES (?,?,?) '''
        c.execute(sql, (accession, hostID, sequence_length))
        # Insert all cds
        sql = ''' INSERT INTO cds (contig, locus_tag, product, translation, cds_start, cds_end) VALUES (?,?,?,?,?,?) '''
        c.executemany(sql, cds_list)
        # Insert the genome sequence PFAMS
        sql = ''' INSERT INTO pfams (locus_tag, pfamnumber, pfamstart, pfamend, strand, contig) VALUES(?,?,?,?,?,?) '''
        c.executemany(sql, gpfamsequence)
        c.close()
    return hostID


def add_number_of_contigs_and_L50_to_host_table(conn, hostID):
    '''
    '''
    with conn:
        # Get the sequence lengths of all contigs belonging to that host
        c = conn.cursor()
        sql = ''' SELECT sequence_length
                    FROM contigs 
                   WHERE hostID=?
                ORDER BY sequence_length DESC '''
        rows = c.execute(sql,(hostID,)).fetchall()
        number_of_contigs = len(rows)
        sequence_lengths = [x[0] for x in rows]

        # Calculate L50 value
        total_seq_length = sum(sequence_lengths)
        partial_seq_length = 0
        for i in range(len(sequence_lengths)):
            partial_seq_length += sequence_lengths[i]
            if partial_seq_length > total_seq_length/2:
                L50 = i+1
                break

        # Write to database
        sql = ''' UPDATE hosts
                     SET number_of_contigs=?, L50=?
                   WHERE hostID=? '''
        c.execute(sql, (number_of_contigs, L50, hostID))



def print_not_imported_genomes_to_file(not_imported_genomes):
    """ """
    if test_flag:
        outfile_path = os.path.join(get_base_dir(), "results", "not_imported_genome_files_test.txt")
    else: 
        outfile_path = os.path.join(get_base_dir(), "results", "not_imported_genome_files.txt")
    with open(outfile_path, "w") as outfile:
        for filename in not_imported_genomes:
            print(filename, file=outfile)


def main(test_flag, database):

    # Get all genome files
    genomes_dir = os.path.join(get_base_dir(), "data", "genomes", "Actinobacteria_internal", "*.gbk")
    genbank_files = glob.glob(genomes_dir)
    if test_flag:
        genbank_files = genbank_files[:10]
    print("{}: Importing {} genomes into the database '{}'.".format(datetime.now().strftime("%d/%m/%Y %H:%M:%S"), len(genbank_files), database))

 
    # Import the genomes to the database
    conn = connect_to_db(database)
    not_imported_genomes = list()
    for i in range(len(genbank_files)):
        genbank_file = genbank_files[i]
        try:
            for rec in SeqIO.parse(genbank_file, "genbank"):
                description, accession, sequence_length, cds_list, gpfamsequence = extract_information_from_gbk_record(rec)
                hostID = insert_record_to_DB(conn, description, accession, sequence_length, cds_list, gpfamsequence)
            add_number_of_contigs_and_L50_to_host_table(conn, hostID)
        except:
            traceback.print_exc()
            not_imported_genomes.append(genbank_file)
            print("{}: Following file could not be imported to the database: {}".format(datetime.now().strftime("%d/%m/%Y %H:%M:%S"), genbank_file))
        if i+1 % 20 == 0 or i+1 == len(genbank_files):
            print("{}: {}/{} genomes done".format(datetime.now().strftime("%d/%m/%Y %H:%M:%S"), i+1, len(genbank_files)))
    conn.close()
    
    # Print the genome files that could not be imported to a file
    print_not_imported_genomes_to_file(not_imported_genomes)


# Main program ================================================================= 

if __name__ == "__main__":

    print("Commandline input:", " ".join(sys.argv))

    parser = argparse.ArgumentParser()
    parser.add_argument('-db', action="store", dest="database", type=str, default="database.db", help='Name of the sqlite database (default=database.db')
    parser.add_argument('--test', action="store_true", help="creates a test database with only 20 hosts")
    
    args = parser.parse_args()
    database = args.database
    test_flag = args.test

    print("{}: Program started. Database: {}".format(datetime.now().strftime("%d/%m/%Y %H:%M:%S"), database))
    main(test_flag, database=database)
    print("{}: Program finished".format(datetime.now().strftime("%d/%m/%Y %H:%M:%S")))