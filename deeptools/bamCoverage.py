#!/usr/bin/env python
# -*- coding: utf-8 -*-

# own tools
import argparse
from deeptools import writeBedGraph  # This should be made directly into a bigWig
from deeptools import parserCommon
from deeptools import bamHandler

debug = 0


def parseArguments():
    parentParser = parserCommon.getParentArgParse()
    bamParser = parserCommon.read_options()
    normalizationParser = parserCommon.normalization_options()
    requiredArgs = get_required_args()
    optionalArgs = get_optional_args()
    outputParser = parserCommon.output()
    parser = \
        argparse.ArgumentParser(
            parents=[requiredArgs, outputParser, optionalArgs,
                     parentParser, normalizationParser, bamParser],
            formatter_class=argparse.ArgumentDefaultsHelpFormatter,
            description='This tool takes an alignment of reads or fragments '
            'as input (BAM file) and generates a coverage track (bigWig or '
            'bedGraph). '
            'The coverage is calculated as the number of reads per bin, which '
            'are consecutive windows of a defined size. It is possible to '
            'extended the length of the reads in this step to better reflect '
            'the actual fragment length. bamCoverage offer normalization by '
            'scaling factor, Reads Per Kilobase per Million mapped reads '
            '(RPKM), or 1x depth of coverage (RPGC).\n',
            usage='An example usage is: %(prog)s -b signal.bam -o signal.bw',
            add_help=False)

    return parser


def get_required_args():
    parser = argparse.ArgumentParser(add_help=False)

    required = parser.add_argument_group('Required arguments')

    # define the arguments
    required.add_argument('--bam', '-b',
                          help='BAM file to process',
                          metavar='BAM file',
                          required=True)

    return parser


def get_optional_args():

    parser = argparse.ArgumentParser(add_help=False)
    optional = parser.add_argument_group('Optional arguments')

    optional.add_argument("--help", "-h", action="help",
                          help="show this help message and exit")

    optional.add_argument('--bamIndex', '-bai',
                          help='Index for the BAM file. Default is to consider '
                          'the path of the BAM file adding the .bai suffix.',
                          metavar='BAM file index')

    optional.add_argument('--scaleFactor',
                          help='Indicate a number that you would like to use. When used in combination '
                          'with --normalizeTo1x or --normalizeUsingRPKM, the computed '
                          'scaling factor will be multiplied by the given scale factor.',
                          default=1.0,
                          type=float,
                          required=False)

    optional.add_argument('--MNase',
                          help='Determine nucleosome positions from MNase-seq data. '
                          'Only 3 nucleotides at the center of each fragment are counted. '
                          'The fragment ends are defined by the two mate reads. '
                          '*NOTE*: Requires paired-end data.',
                          action='store_true')

    return parser


def scaleFactor(string):
    try:
        scalefactor1, scalefactor2 = string.split(":")
        scalefactors = (float(scalefactor1), float(scalefactor2))
    except:
        raise argparse.ArgumentTypeError(
            "Format of scaleFactors is factor1:factor2. "
            "The value given ( {} ) is not valid".format(string))

    return scalefactors


def process_args(args=None):
    args = parseArguments().parse_args(args)

    if args.scaleFactor != 1:
        args.normalizeTo1x = None
    if args.smoothLength and args.smoothLength <= args.binSize:
        print "Warning: the smooth length given ({}) is smaller than the bin "\
            "size ({}).\n\n No smoothing will be done".format(args.smoothLength, args.binSize)
        args.smoothLength = None

    return args


def get_scale_factor(args):

    scale_factor = args.scaleFactor
    bam_handle = bamHandler.openBam(args.bam, args.bamIndex)
    bam_mapped = parserCommon.bam_total_reads(bam_handle, args.ignoreForNormalization)

    if args.normalizeTo1x:
        # try to guess fragment length if the bam file contains paired end reads
        from deeptools.getFragmentAndReadSize import get_read_and_fragment_length
        frag_len_dict, read_len_dict = get_read_and_fragment_length(args.bam, args.bamIndex,
                                                                    return_lengths=False,
                                                                    numberOfProcessors=args.numberOfProcessors,
                                                                    verbose=args.verbose)
        if args.extendReads:
            if args.extendReads is True:
                # try to guess fragment length if the bam file contains paired end reads
                if frag_len_dict:
                    fragment_length = frag_len_dict['median']
                else:
                    exit("*ERROR*: library is not paired-end. Please provide an extension length.")
                if args.verbose:
                    print("Fragment length based on paired en data "
                          "estimated to be {}".format(frag_len_dict['median']))

            elif args.extendReads < 1:
                exit("*ERROR*: read extension must be bigger than one. Value give: {} ".format(args.extendReads))
            elif args.extendReads > 2000:
                exit("*ERROR*: read extension must be smaller that 2000. Value give: {} ".format(args.extendReads))
            else:
                fragment_length = args.extendReads

        else:
            # set as fragment length the read length
            fragment_length = int(read_len_dict['median'])
            if args.verbose:
                print "Estimated read length is {}".format(int(read_len_dict['median']))

        current_coverage = \
            float(bam_mapped * fragment_length) / args.normalizeTo1x
        # the scaling sets the coverage to match 1x
        scale_factor *= 1.0 / current_coverage
        if debug:
            print "Estimated current coverage {}".format(current_coverage)
            print "Scaling factor {}".format(args.scaleFactor)

    elif args.normalizeUsingRPKM:
        # the RPKM is the # reads per tile / \
        #    ( total reads (in millions) * tile length in Kb)
        million_reads_mapped = float(bam_mapped) / 1e6
        tile_len_in_kb = float(args.binSize) / 1000

        scale_factor *= 1.0 / (million_reads_mapped * tile_len_in_kb)

        if debug:
            print "scale factor using RPKM is {0}".format(args.scaleFactor)

    return scale_factor


def main(args=None):
    args = process_args(args)

    global debug
    if args.verbose:
        debug = 1
    else:
        debug = 0

    func_args = {'scaleFactor': get_scale_factor(args)}
    zeros_to_nans = not args.keepNAs
    if args.MNase:
        # check that library is paired end
        # using getFragmentAndReadSize
        from deeptools.getFragmentAndReadSize import get_read_and_fragment_length
        frag_len_dict, read_len_dict = get_read_and_fragment_length(args.bam, args.bamIndex,
                                                                    return_lengths=False,
                                                                    numberOfProcessors=args.numberOfProcessors,
                                                                    verbose=args.verbose)
        if frag_len_dict is None:
            exit("*Error*: For the --MNAse function a paired end library is required. ")

        wr = CenterFragment([args.bam],
                            binLength=args.binSize,
                            stepSize=args.binSize,
                            region=args.region,
                            numberOfProcessors=args.numberOfProcessors,
                            extendReads=args.extendReads,
                            minMappingQuality=args.minMappingQuality,
                            ignoreDuplicates=args.ignoreDuplicates,
                            center_read=args.centerReads,
                            zerosToNans=zeros_to_nans,
                            samFlag_include=args.samFlagInclude,
                            samFlag_exclude=args.samFlagExclude,
                            verbose=args.verbose,
                            )

    else:
        wr = writeBedGraph.WriteBedGraph([args.bam],
                                         binLength=args.binSize,
                                         stepSize=args.binSize,
                                         region=args.region,
                                         numberOfProcessors=args.numberOfProcessors,
                                         extendReads=args.extendReads,
                                         minMappingQuality=args.minMappingQuality,
                                         ignoreDuplicates=args.ignoreDuplicates,
                                         center_read=args.centerReads,
                                         zerosToNans=zeros_to_nans,
                                         samFlag_include=args.samFlagInclude,
                                         samFlag_exclude=args.samFlagExclude,
                                         verbose=args.verbose,
                                         )

    wr.run(writeBedGraph.scaleCoverage, func_args, args.outFileName,
           format=args.outFileFormat, smooth_length=args.smoothLength)


class CenterFragment(writeBedGraph.WriteBedGraph):
    """
    Class to redefine the get_fragment_from_read for the --MNase case

    The coverage of the fragment is defined as the 2 or 3 basepairs at the
    center of the fragment length. d
    """
    def get_fragment_from_read(self, read):
        """
        Takes a proper pair fragment of high quality and limited
        to a certain length and outputs the center
        """
        fragment_start = fragment_end = None

        # only paired forward reads are considered
        if read.is_proper_pair and not read.is_reverse and abs(read.tlen) < 250:
            # distance between pairs is even return two bases at the center
            if read.tlen % 2 == 0:
                fragment_start = read.pos + read.tlen / 2 - 1
                fragment_end = fragment_start + 2

            # distance is odd return three bases at the center
            else:
                fragment_start = read.pos + read.tlen / 2 - 1
                fragment_end = fragment_start + 3

        return [(fragment_start, fragment_end)]
