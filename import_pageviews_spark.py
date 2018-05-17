#!/usr/bin/env python3

# CREATE TABLE pageviews (
#     lang varchar(8),
#     request test,
#     timestamp timestamp,
#     views integer CHECK (views>0),
#     reqbytes integer CHECK (reqbytes>0)
# );

# How to copy from CSV file to PostgreSQL table with headers in CSV file?
# https://stackoverflow.com/q/17662631/2377454

initial_comment=\
"""# Wikimedia page request counts for 16/11/2011 (dd/mm/yyyy)
#
# Each line shows 'project page daily-total hourly-counts'
#
# Project is 'language-code project-code'
#
# Project-code is
#
# b:wikibooks,
# k:wiktionary,
# n:wikinews,
# q:wikiquote,
# s:wikisource,
# v:wikiversity,
# wo:wikivoyage,
# z:wikipedia (z added by merge script: wikipedia happens to be sorted last in dammit.lt files, but without suffix)
#
# Counts format: only hours with page view count > 0 (or data missing) are represented,
#
# Hour 0..23 shown as A..X (saves up to 22 bytes per line compared to comma separated values), followed by view count.
# If data are missing for some hour (file missing or corrupt) a question mark (?) is shown,
# and a adjusted daily total is extrapolated as follows: for each missing hour the total is incremented with hourly average
#
# Page titles are shown unmodified (preserves sort sequence)
#"""

import pandas as pd
import argparse
import datetime
import tempfile
import gzip
import csv
import os
import logging
import progressbar

progressbar.streams.wrap_stderr()

import findspark
findspark.init()

import pyspark
from pyspark.sql.types import StructType, StructField
from pyspark.sql.types import StringType, IntegerType, TimestampType

########## logging
# create logger with 'spam_application'
logger = logging.getLogger(__file__)
logger.setLevel(logging.DEBUG)

# create console handler with a higher log level
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)

# create formatter and add it to the handlers
formatter = logging.Formatter('[%(asctime)s][%(levelname)s]: %(message)s')
ch.setFormatter(formatter)

# add the handlers to the logger
logger.addHandler(ch)
##########


def unionAll(*dfs):
    first, *_ = dfs  # Python 3.x, for 2.x you'll have to unpack manually
    return first.sql_ctx.createDataFrame(
        first.sql_ctx._sc.union([df.rdd for df in dfs]),
        first.schema
    )


def date_parser(timestamp):
    return datetime.datetime.strptime(timestamp, '%Y%m%d-%H%M%S')


def cli_args():
    parser = argparse.ArgumentParser()

    parser.add_argument("FILE",
                        help="Input file.",
                        nargs='+')
    args = parser.parse_args()

    parser.add_argument("--encoding",
                        help="Encoding of input files.",
                        default='utf-8',
                        nargs='+')
    args = parser.parse_args()

    return args

if __name__ == "__main__":
    args = cli_args()

    sc = pyspark.SparkContext(appName="merge-pagecounts")
    sqlctx = pyspark.SQLContext(sc)

    schema = StructType([StructField("lang", StringType(), False),
                         StructField("page", StringType(), False),
                         StructField("views", IntegerType(), False),
                         StructField("timestamp", TimestampType(), True)])

    input_files = args.FILE
    encoding = args.encoding

    list_dfs = list()
    for input_file in input_files:
        logger.info('Processing file: {}'.format(input_file))

        with gzip.open(input_file, "rt", encoding=encoding, errors='replace') as infile:
            num_lines = sum(1 for line in infile)

        logger.debug('num_lines: {}'.format(num_lines))

        timestamp = date_parser(os.path.basename(input_file)
                                       .replace('pagecounts-','')
                                       .replace('.gz',''))

        with tempfile.NamedTemporaryFile(mode='w+', encoding=encoding) \
                as uncompressed_file:

            writer = csv.writer(uncompressed_file, delimiter='\t', quoting=csv.QUOTE_ALL)
            with gzip.open(input_file, "rt", encoding=encoding, errors='replace') as infile:
                reader = csv.reader(infile, delimiter=' ')

                lncount = 0
                with progressbar.ProgressBar(max_value=num_lines) as bar:
                    while True:
                        lncount += 1
                        bar.update(lncount)

                        try:
                            line = next(reader)
                        except StopIteration:
                            break
                        except:
                            continue

                        try:
                            lang = line[0]
                            page = line[1]
                            views = int(line[2])
                        except:
                            pass

                        writer.writerow((lang, page, views))

                uncompressed_file.seek(0)

                # import ipdb; ipdb.set_trace()
                tmp_df = pd.read_csv(uncompressed_file,
                                     sep='\t',
                                     names=['lang', 'page', 'views'],
                                     dtype={'lang': str,
                                            'page': str,
                                            'views': int,
                                            },
                                     header=None,
                                     encoding='utf-8'
                                     )

                tmp_df['timestamp'] = timestamp

                logger.info('Converting pandas DataFrame to Spark DataFrame.')
                tmp_spark_df = sqlctx.createDataFrame(tmp_df,schema=schema)
                list_dfs.append(tmp_spark_df)
                del tmp_df

                logger.info('Added DataFrame for file {} to list'.format(input_file))

    # logger.info('Concatenate pandas.DataFrames')
    # pddf = pd.concat(list_dfs)
    # logger.info('pandas.DataFrames concatenated')

    logger.info('Union of all Spark DataFrames.')
    df = unionAll(*list_dfs)

    logger.info('Spark DataFrame created')
