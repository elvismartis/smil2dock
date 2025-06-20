#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SMILES-to-3D Molecular Format Converter

For Bug reports, send an E-mail to:
elvis.afmartis@gmail.com
"""

import os
import argparse
import logging
from rdkit import Chem
from rdkit.Chem import AllChem, Descriptors, DataStructs, rdMolDescriptors, Crippen
from rdkit.Chem.Fingerprints import FingerprintMols
from openbabel import pybel
import dimorphite_dl

# Ensure logging is configured
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def ensure_output_dir(output_base):
    """Create the output directory if it does not exist."""
    out_dir = os.path.dirname(output_base)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

def protonate_smiles(smiles, ph_min=6.4, ph_max=8.4, precision=1.0, max_variants=128):
    """Protonate SMILES using Dimorphite-DL for specified pH range"""
    try:
        protonated_smiles = dimorphite_dl.protonate_smiles(
            smiles, 
            ph_min=ph_min, 
            ph_max=ph_max, 
            precision=precision,
            max_variants=max_variants
        )
        return protonated_smiles
    except Exception as e:
        print(f"Error protonating {smiles}: {str(e)}")
        return [smiles]  # Return original if protonation fails

def smiles_to_3d(smiles, output_base="molecule", num_confs=10, optimize=True, protonate=False, ph_min=6.4, ph_max=8.4):
    """Convert SMILES to multiple 3D formats and calculate properties"""
    protonated_variants = None  # Always define so return is consistent
    try:
        # Protonate SMILES if requested
        if protonate:
            protonated_variants = protonate_smiles(smiles, ph_min, ph_max)
            print(f"Generated {len(protonated_variants)} protonation states for pH {ph_min}-{ph_max}")
            for i, variant in enumerate(protonated_variants):
                print(f"  Variant {i+1}: {variant}")
            
            # Use the first variant for 3D generation (or let user choose)
            if len(protonated_variants) > 1:
                print(f"Using first protonation variant: {protonated_variants[0]}")
            smiles = protonated_variants[0]
        else:
            protonated_variants = [smiles]

        # RDKit: Parse and add hydrogens
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            logging.error(f"Invalid SMILES: {smiles}")
            return None, None, protonated_variants
        mol = Chem.AddHs(mol)
        # Generate 3D conformers
        try:
            AllChem.EmbedMultipleConfs(
                mol,
                numConfs=num_confs,
                randomSeed=42,
                enforceChirality=True
            )
        except Exception as e:
            logging.error(f"Conformer generation failed for {smiles}: {e}")
            return None, None, protonated_variants

        if optimize:
            for conf_id in range(mol.GetNumConformers()):
                try:
                    AllChem.MMFFOptimizeMolecule(
                        mol,
                        confId=conf_id,
                        mmffVariant='MMFF94s',
                        maxIters=1000
                    )
                except Exception as e:
                    logging.warning(f"Optimization failed for conformer {conf_id} of {smiles}: {e}")

        # Convert to OpenBabel molecule for format export
        try:
            sdf_data = Chem.MolToMolBlock(mol)
            ob_mol = pybel.readstring("mol", sdf_data)
        except Exception as e:
            logging.error(f"OpenBabel conversion failed: {e}")
            return None, None, protonated_variants

        # Ensure output directory exists
        ensure_output_dir(output_base)

        # Write different file formats
        for fmt in ["pdb", "mol2", "sdf", "pdbqt"]:
            output = f"{output_base}.{fmt}"
            try:
                ob_mol.write(fmt, output, overwrite=True)
            except Exception as e:
                logging.error(f"Failed to write {output}: {e}")

        # Calculate properties (corrected descriptors)
        try:
            properties = {
                "Molecular Weight": Descriptors.ExactMolWt(mol),
                "Crippen_LogP": Crippen.MolLogP(mol),
                "Crippen_MR": Crippen.MolMR(mol),
                "H-Bond Donors": Descriptors.NumHDonors(mol),
                "H-Bond Acceptors": Descriptors.NumHAcceptors(mol),
                "TPSA": Descriptors.TPSA(mol),
                "Rotatable Bonds": Descriptors.NumRotatableBonds(mol),
                "#Aliphatic Rings": rdMolDescriptors.CalcNumAliphaticRings(mol),
                "#Aromatic Rings": rdMolDescriptors.CalcNumAromaticRings(mol),
                "#Heteroaromatic Rings": rdMolDescriptors.CalcNumHeterocycles(mol)
            }
        except Exception as e:
            logging.error(f"Property calculation failed: {e}")
            properties = {}

        return mol, properties, protonated_variants

    except Exception as e:
        logging.error(f"Error processing {smiles}: {e}")
        return None, None, protonated_variants

def calculate_tanimoto_similarity(mol1, mol2, fp_type="morgan", radius=2, n_bits=2048):
    """Calculate Tanimoto similarity between two molecules"""
    if mol1 is None or mol2 is None:
        logging.warning("One or both molecules are None, cannot calculate similarity.")
        return None
    if fp_type == "morgan":
        fp1 = AllChem.GetMorganFingerprintAsBitVect(mol1, radius, nBits=n_bits)
        fp2 = AllChem.GetMorganFingerprintAsBitVect(mol2, radius, nBits=n_bits)
    else:
        fp1 = FingerprintMols.FingerprintMol(mol1)
        fp2 = FingerprintMols.FingerprintMol(mol2)
    return DataStructs.TanimotoSimilarity(fp1, fp2)

def batch_process(input_file, output_dir, reference_smiles=None, fp_type="morgan", radius=2, n_bits=2048, 
                 protonate=False, ph_min=6.4, ph_max=8.4, num_confs=10):
    """Process a file of SMILES strings with optional protonation and similarity calculation"""
    if reference_smiles:
        ref_mol = Chem.MolFromSmiles(reference_smiles)
        if ref_mol is None:
            logging.error(f"Invalid reference SMILES: {reference_smiles}")
            ref_mol = None
    else:
        ref_mol = None

    # Create protonation output file if requested
    protonation_file = None
    if protonate:
        protonation_file = open(os.path.join(output_dir, "protonation_states.txt"), 'w')
        protonation_file.write(f"Original_SMILES\tProtonated_SMILES\tpH_Range\n")

    with open(input_file) as f:
        for idx, line in enumerate(f):
            smiles = line.strip()
            if smiles:
                base_name = os.path.join(output_dir, f"mol_{idx+1}")
                mol, props, protonated_variants = smiles_to_3d(
                    smiles, base_name, num_confs, protonate=protonate, ph_min=ph_min, ph_max=ph_max
                )
                if props:
                    print(f"Processed {smiles}")
                    print("Properties:", props)

                    # Log protonation states
                    if protonate and protonated_variants and protonation_file:
                        for variant in protonated_variants:
                            protonation_file.write(f"{smiles}\t{variant}\t{ph_min}-{ph_max}\n")

                    # Calculate similarity if reference provided
                    if ref_mol:
                        similarity = calculate_tanimoto_similarity(mol, ref_mol, fp_type, radius, n_bits)
                        if similarity is not None:
                            print(f"Tanimoto similarity to reference: {similarity:.4f}")

    if protonation_file:
        protonation_file.close()
        print(f"Protonation states saved to: {os.path.join(output_dir, 'protonation_states.txt')}")

def single_process(smiles, output_base, num_confs, reference_smiles=None, fp_type="morgan", radius=2, n_bits=2048,
                  protonate=False, ph_min=6.4, ph_max=8.4):
    """Process a single SMILES string with optional protonation and similarity calculation"""
    mol, props, protonated_variants = smiles_to_3d(
        smiles, output_base, num_confs, protonate=protonate, ph_min=ph_min, ph_max=ph_max
    )

    if props:
        print("Generated files:", [f"{output_base}.{fmt}" for fmt in ["pdb", "mol2", "sdf", "pdbqt"]])
        print("\nMolecular Properties:")
        for k, v in props.items():
            print(f"{k}: {v:.2f}")

        # Display protonation states
        if protonate and protonated_variants:
            print(f"\nProtonation states at pH {ph_min}-{ph_max}:")
            for i, variant in enumerate(protonated_variants, 1):
                print(f"  {i}. {variant}")

        # Calculate similarity if reference provided
        if reference_smiles:
            ref_mol = Chem.MolFromSmiles(reference_smiles)
            if ref_mol:
                similarity = calculate_tanimoto_similarity(mol, ref_mol, fp_type, radius, n_bits)
                if similarity is not None:
                    print(f"Tanimoto similarity to reference: {similarity:.4f}")
            else:
                print(f"Invalid reference SMILES: {reference_smiles}")

def is_valid_smiles(smiles):
    """Check if a string is a valid SMILES using RDKit."""
    return Chem.MolFromSmiles(smiles) is not None

if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description='SMILES to 3D converter with protonation, property calculation, and similarity analysis'
    )

    # Core arguments
    parser.add_argument('-i', '--input', help='SMILES string or input file')
    parser.add_argument('-o', '--output', default="output", help='Output directory or base name')
    parser.add_argument('-n', '--num_confs', type=int, default=10, help='Number of conformers to generate')

    # Protonation arguments
    parser.add_argument('--protonate', action='store_true', help='Enable protonation using Dimorphite-DL')
    parser.add_argument('--ph_min', type=float, default=6.4, help='Minimum pH for protonation (default: 6.4)')
    parser.add_argument('--ph_max', type=float, default=8.4, help='Maximum pH for protonation (default: 8.4)')
    parser.add_argument('--precision', type=float, default=1.0, help='pKa precision factor (default: 1.0)')
    parser.add_argument('--max_variants', type=int, default=128, help='Maximum protonation variants (default: 128)')

    # Similarity arguments
    parser.add_argument('--reference', help='Reference SMILES for Tanimoto similarity')
    parser.add_argument('--fp_type', choices=["morgan", "rdkit"], default="morgan", help='Fingerprint type')
    parser.add_argument('--radius', type=int, default=2, help='Morgan fingerprint radius (default: 2)')
    parser.add_argument('--bits', type=int, default=2048, help='Fingerprint bit size (default: 2048)')

    args = parser.parse_args()

    if os.path.isfile(args.input):
        os.makedirs(args.output, exist_ok=True)
        batch_process(
            args.input, args.output, args.reference, args.fp_type, args.radius, args.bits,
            args.protonate, args.ph_min, args.ph_max, args.num_confs
        )
    else:
        single_process(
            args.input, args.output, args.num_confs, args.reference, args.fp_type, args.radius, args.bits,
            args.protonate, args.ph_min, args.ph_max
        )
